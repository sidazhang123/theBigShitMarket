"""实时涨幅轮询。

每轮结束后再等待配置的间隔，然后开始下一轮。不并行两次拉取。
按配置优先级尝试标准行情服务器，整站快照失败后再换下一站。
只保留涨幅数组，用完即释放行情表。
"""

from __future__ import annotations

import socket
import math
import struct
import threading
import time
import zlib
from datetime import datetime
from dataclasses import dataclass
from typing import Callable

from app.config import QuoteHost
from app.count import summarize_pct
from app.quote_client import QuoteCancelled, fetch_quote_data


def describe_quote_error(exc: BaseException) -> str:
    """输入：一轮行情抛出的异常。
    输出：给右上角红窗的一行说明。
    用途：报告全部失败的端点，普通异常区分连接、解析和其它故障。
    边界：普通异常原文超过 72 字时截断；聚合错误保留全部端点。空异常信息只留类别。
    """
    if isinstance(exc, QuoteHostsError):
        endpoints = "、".join(f"{host.host}:{host.port}" for host, _ in exc.attempts)
        return f"行情服务器全部失败：{endpoints}"
    detail = " ".join(str(exc).split())
    if len(detail) > 72:
        detail = detail[:71] + "…"
    if _is_connect_error(exc):
        title = "网络连接失败"
    elif _is_parse_error(exc):
        title = "数据解析错误"
    else:
        title = "其它错误"
    if not detail:
        return title
    return f"{title}：{detail}"


def _is_connect_error(exc: BaseException) -> bool:
    """输入：异常。
    输出：是否属于连不上行情服务器。
    用途：红窗使用「网络连接失败」。
    边界：只看异常类型和文案，不再次连接。
    """
    if isinstance(exc, (TimeoutError, ConnectionError, socket.gaierror)):
        return True
    text = str(exc).lower()
    marks = (
        "连接失败",
        "连接已断开",
        "timed out",
        "timeout",
        "connection refused",
        "connection reset",
        "network is unreachable",
    )
    return any(mark in text for mark in marks)


def _is_parse_error(exc: BaseException) -> bool:
    """输入：异常。
    输出：是否属于已经拿到字节但解不开。
    用途：红窗使用「数据解析错误」。
    边界：连接类异常即使文案里带分页字样，也不算解析错误。
    """
    if isinstance(exc, (zlib.error, struct.error, UnicodeError)):
        return True
    text = str(exc)
    marks = ("解析", "解压", "压缩", "正文", "条数不符", "满页条数", "缺少行情页")
    return any(mark in text for mark in marks)


class QuoteHostsError(RuntimeError):
    """一轮中所有标准行情服务器都失败，保留每个端点和原始异常。"""

    def __init__(self, attempts: list[tuple[QuoteHost, Exception]]) -> None:
        """输入：有序尝试及异常；输出：聚合错误；用途：界面和日志报告失败；边界：不保留行情数组。"""
        self.attempts = tuple(attempts)
        details = "; ".join(f"{host.host}:{host.port} [{exc}]" for host, exc in attempts)
        super().__init__(f"行情服务器全部失败: {details}" if details else "没有配置行情服务器")


@dataclass(frozen=True)
class Snapshot:
    """完整单站快照：有效计数、原始股票覆盖和实际端点。"""

    counts: list[int]
    universe: int
    raw_stocks: int
    host: QuoteHost


def fetch_quote_snapshot(hosts: tuple[QuoteHost, ...], timeout: float, *, min_stocks: int = 4500,
                         min_coverage: float = .9, previous_raw_stocks: int = 0,
                         deadline_seconds: float = 30, cancel: threading.Event | None = None) -> Snapshot:
    """输入：按优先级排列的标准行情端点、超时秒数。
    输出：含 11 档家数、有效数量、原始数量和来源端点的快照。
    用途：每轮从第一台开始取完整快照，失败后顺序尝试下一台。
    边界：协议或原始覆盖不完整则换站；完整帧可没有有效成交。取消不换站，全部失败抛出聚合异常。
    """
    attempts: list[tuple[QuoteHost, Exception]] = []
    for host in hosts:
        if cancel is not None and cancel.is_set():
            raise QuoteCancelled("行情请求已取消")
        try:
            data = fetch_quote_data(host.host, host.port, timeout, deadline_seconds=deadline_seconds, cancel=cancel)
            required = max(min_stocks, math.ceil(previous_raw_stocks * min_coverage))
            if data.raw_stocks < required:
                raise RuntimeError(f"原始股票覆盖不足: {data.raw_stocks}/{required}")
            counts, universe = summarize_pct(data.pcts)
            return Snapshot(counts, universe, data.raw_stocks, host)
        except QuoteCancelled:
            raise
        except Exception as exc:
            attempts.append((host, exc))
    raise QuoteHostsError(attempts)


class LivePoller:
    """后台轮询。回调发生在轮询线程，调用方自己同步界面状态。"""

    def __init__(
        self,
        interval_seconds: int,
        on_quote: Callable[[list[int], int, datetime], None],
        on_error: Callable[[Exception], None],
        hosts: tuple[QuoteHost, ...],
        timeout: float,
        *, min_stocks: int = 4500, min_coverage: float = .9, deadline_seconds: float = 30,
        on_refreshing: Callable[[bool], None] | None = None,
    ) -> None:
        """输入：间隔秒数、成功回调、失败回调、有序端点和超时。
        输出：未启动的轮询器。
        用途：按固定间隔重复取实时涨幅。
        边界：间隔小于 1 时按 1 秒等待。回调抛出的异常会结束本轮并进入失败回调。
        """
        self._interval = max(1, int(interval_seconds))
        self._on_quote = on_quote
        self._on_error = on_error
        self._hosts = tuple(hosts)
        self._timeout = float(timeout)
        self._min_stocks = min_stocks
        self._min_coverage = min_coverage
        self._deadline_seconds = deadline_seconds
        self._on_refreshing = on_refreshing
        self.last_snapshot: Snapshot | None = None
        self._refresh_requested = False
        self._callback_lock = threading.RLock()
        self._stop = threading.Event()
        self._schedule = threading.Condition()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """输入：无。
        输出：无。
        用途：启动守护线程并立刻拉第一轮。
        边界：重复调用不会启动第二条线程。
        """
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="meter-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """输入：无。
        输出：无。
        用途：通知轮询线程在当前等待处退出。
        边界：取消信号使连接关闭并中断请求；不阻塞界面等待线程退出，停止后不再回调。
        """
        with self._callback_lock:
            self._stop.set()
        with self._schedule:
            self._schedule.notify_all()

    def request_refresh(self) -> None:
        """输入输出：无；用途：唤醒等待或在当前请求结束后补一轮；边界：合并重复点击，不并发抓取。"""
        with self._schedule:
            self._refresh_requested = True
            self._schedule.notify_all()

    def _refreshing(self, active: bool) -> None:
        """输入：是否请求中；输出：无；用途：通知界面刷新状态；边界：停止后不回调，界面异常隔离。"""
        with self._callback_lock:
            if self._stop.is_set() or self._on_refreshing is None:
                return
            try:
                self._on_refreshing(active)
            except Exception:
                pass

    def set_interval(self, seconds: int) -> None:
        """输入：正整数秒数；输出：无。
        用途：唤醒等待中的轮询器，从上轮结束时重新计算下次取数时刻。
        边界：不打断正在执行的请求，不产生并行请求；小于 1 时按 1 秒。
        """
        with self._schedule:
            self._interval = max(1, int(seconds))
            self._schedule.notify_all()

    def _wait_next(self) -> bool:
        """输入：无；输出：是否收到了停止信号。
        用途：按最新间隔等待，下拉缩短/延长间隔会立即调整剩余等待时间。
        边界：使用单调时钟；设置改变不重置上轮结束时刻。
        """
        finished = time.monotonic()
        with self._schedule:
            while not self._stop.is_set():
                if self._refresh_requested:
                    self._refresh_requested = False
                    return False
                remaining = self._interval - (time.monotonic() - finished)
                if remaining <= 0:
                    return False
                self._schedule.wait(remaining)
            return True

    def _loop(self) -> None:
        """输入：无。
        输出：无。
        用途：拉取一帧，然后从该轮结束时刻起等待完整间隔。
        边界：停止信号在等待期间到达时立即退出。上一轮未结束时不会开始下一轮。
        """
        while not self._stop.is_set():
            self._refreshing(True)
            try:
                snapshot = fetch_quote_snapshot(
                    self._hosts, self._timeout, min_stocks=self._min_stocks,
                    min_coverage=self._min_coverage,
                    previous_raw_stocks=self.last_snapshot.raw_stocks if self.last_snapshot else 0,
                    deadline_seconds=self._deadline_seconds, cancel=self._stop,
                )
            except QuoteCancelled:
                return
            except Exception as exc:
                with self._callback_lock:
                    if not self._stop.is_set():
                        try:
                            self._on_error(exc)
                        except Exception:
                            pass
            else:
                with self._callback_lock:
                    if not self._stop.is_set():
                        self.last_snapshot = snapshot
                        try:
                            self._on_quote(snapshot.counts, snapshot.universe, datetime.now())
                        except Exception as exc:
                            try:
                                self._on_error(exc)
                            except Exception:
                                pass
            finally:
                self._refreshing(False)
            if self._wait_next():
                return
