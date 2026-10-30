"""实时涨幅轮询。

每轮结束后再等待配置的间隔，然后开始下一轮。不并行两次拉取。
只保留涨幅数组，用完即释放行情表。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable

from app.count import summarize_pct
from app.quote_client import fetch_change_pcts


def fetch_quote_snapshot(host: str, port: int, timeout: float) -> tuple[list[int], int]:
    """输入：标准行情地址、端口、超时秒数。
    输出：11 档家数，以及有限涨幅的股票数。
    用途：从配置的那一台行情服务器取一帧涨幅。
    边界：连接或分页失败时抛出异常。不保留行情表。
    """
    return summarize_pct(fetch_change_pcts(host, port, timeout))


class LivePoller:
    """后台轮询。回调发生在轮询线程，调用方自己同步界面状态。"""

    def __init__(
        self,
        interval_seconds: int,
        on_quote: Callable[[list[int], int, datetime], None],
        on_error: Callable[[Exception], None],
        host: str,
        port: int,
        timeout: float,
    ) -> None:
        """输入：间隔秒数、成功回调、失败回调。
        输出：未启动的轮询器。
        用途：按固定间隔重复取实时涨幅。
        边界：间隔小于 1 时按 1 秒等待。回调抛出的异常会结束本轮并进入失败回调。
        """
        self._interval = max(1, int(interval_seconds))
        self._on_quote = on_quote
        self._on_error = on_error
        self._host = host
        self._port = int(port)
        self._timeout = float(timeout)
        self._stop = threading.Event()
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
        边界：不等待正在进行的行情请求结束。
        """
        self._stop.set()

    def _loop(self) -> None:
        """输入：无。
        输出：无。
        用途：拉取一帧，然后从该轮结束时刻起等待完整间隔。
        边界：停止信号在等待期间到达时立即退出。上一轮未结束时不会开始下一轮。
        """
        while not self._stop.is_set():
            try:
                counts, universe = fetch_quote_snapshot(self._host, self._port, self._timeout)
            except Exception as exc:
                self._on_error(exc)
            else:
                try:
                    self._on_quote(counts, universe, datetime.now())
                except Exception as exc:
                    self._on_error(exc)
            if self._stop.wait(self._interval):
                return
