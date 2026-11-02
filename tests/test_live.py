"""行情故障文案。不连网。"""

import socket
import struct
import zlib

import numpy as np
import pytest

from app.config import QuoteHost
from app.quote_client import QuoteData, QuoteCancelled
from app.live import LivePoller, QuoteHostsError, Snapshot, describe_quote_error, fetch_quote_snapshot

HOSTS = (QuoteHost("first", 7709), QuoteHost("second", 7710), QuoteHost("third", 7711))


def test_connect_errors_use_network_title():
    """超时、拒绝连接和连接失败文案都归到网络连接失败。"""
    assert describe_quote_error(TimeoutError("timed out")).startswith("网络连接失败")
    assert describe_quote_error(ConnectionRefusedError(111, "refused")).startswith("网络连接失败")
    assert describe_quote_error(socket.gaierror("name")).startswith("网络连接失败")
    text = describe_quote_error(RuntimeError("行情连接失败: timed out"))
    assert text.startswith("网络连接失败：")
    assert "timed out" in text


def test_parse_errors_use_parse_title():
    """解压失败和分页正文对不上时归到数据解析错误。"""
    assert describe_quote_error(zlib.error("incomplete")).startswith("数据解析错误")
    assert describe_quote_error(struct.error("unpack")).startswith("数据解析错误")
    text = describe_quote_error(RuntimeError("行情响应压缩不完整"))
    assert text.startswith("数据解析错误：行情响应压缩不完整")
    assert describe_quote_error(RuntimeError("行情分页条数不符: 声明 80，定位 3")).startswith("数据解析错误")


def test_other_errors_keep_their_own_title():
    """对不上连接和解析的故障归到其它错误。"""
    assert describe_quote_error(RuntimeError("行情分页没有结束")) == "其它错误：行情分页没有结束"
    assert describe_quote_error(RuntimeError("")) == "其它错误"


def test_changed_interval_recalculates_wait_from_last_completion(monkeypatch):
    """输入：等待中先延长再缩短；输出：剩余时间准确；边界：不重置上轮完成时刻。"""
    now = [0.0]
    waits = []
    poller = LivePoller(120, lambda *args: None, lambda exc: None, HOSTS, 8)

    class Schedule:
        """模拟单调时钟与设置通知，避免测试真实等待数分钟。"""
        def __enter__(self):
            """输入：无；输出：上下文自身；边界：不加真实锁。"""
            return self

        def __exit__(self, *args):
            """输入：退出信息；输出：无；边界：异常正常向外传播。"""

        def notify_all(self):
            """输入输出：无；用途：模拟通知；边界：无后台线程。"""

        def wait(self, timeout):
            """输入：剩余等待；输出：无；用途：推进时钟并模拟用户选择；边界：三次后到期。"""
            waits.append(timeout)
            if len(waits) == 1:
                now[0] = 0.25
                poller.set_interval(300)
            elif len(waits) == 2:
                now[0] = 50
                poller.set_interval(60)
            else:
                now[0] = 60

    poller._schedule = Schedule()
    monkeypatch.setattr("app.live.time.monotonic", lambda: now[0])
    assert poller._wait_next() is False
    assert waits == [120, 299.75, 10]
    poller.stop()
    assert poller._wait_next() is True


def test_stop_wakes_waiting_poller():
    """输入：长周期轮询器停止；输出：等待线程及时退出；边界：无需真实行情。"""
    import threading

    poller = LivePoller(300, lambda *args: None, lambda exc: None, HOSTS, 8)
    waiting = threading.Thread(target=poller._wait_next, daemon=True)
    waiting.start()
    poller.stop()
    waiting.join(timeout=1)
    assert not waiting.is_alive()


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), RuntimeError("握手失败"),
    RuntimeError("缺少行情页 80"), struct.error("unpack")])
def test_snapshot_uses_next_host_after_failure(monkeypatch, failure):
    """输入：第一站连接/握手/分页/解析失败；输出：第二站完整快照；边界：成功后不访问第三站。"""
    calls = []
    def fetch(host, port, timeout, **kwargs):
        """输入：单站端点和超时；输出：完整涨幅；边界：第一站模拟故障。"""
        calls.append((host, port, timeout))
        if host == "first":
            raise failure
        return QuoteData(np.asarray([-16.1, -6.1, 1.0]), 3)
    monkeypatch.setattr("app.live.fetch_quote_data", fetch)
    snapshot = fetch_quote_snapshot(HOSTS, 3, min_stocks=1)
    assert calls == [("first", 7709, 3), ("second", 7710, 3)]
    assert snapshot.universe == 3 and snapshot.counts[0] == 1 and snapshot.counts[-1] == 2


def test_each_round_starts_from_first_host(monkeypatch):
    """输入：首站本轮失败、下轮恢复；输出：下轮优先用首站；边界：不记忆上轮备用站作为首选。"""
    calls = []
    def fetch(host, port, timeout, **kwargs):
        """输入：端点；输出：涨幅；用途：模拟首站恢复；边界：只第一次调用失败。"""
        calls.append(host)
        if len(calls) == 1:
            raise ConnectionRefusedError("first down")
        return QuoteData(np.asarray([-7.0]), 1)
    monkeypatch.setattr("app.live.fetch_quote_data", fetch)
    assert fetch_quote_snapshot(HOSTS, 3, min_stocks=1).universe == 1
    assert fetch_quote_snapshot(HOSTS, 3, min_stocks=1).universe == 1
    assert calls == ["first", "second", "first"]


def test_all_hosts_fail_including_empty_data(monkeypatch):
    """输入：连接失败、空结果和全无效结果；输出：带全部端点的错误；边界：不给界面提交零家数。"""
    calls = []
    def fetch(host, port, timeout, **kwargs):
        """输入：单站端点；输出：空或无效数据；边界：首站连接失败。"""
        calls.append(host)
        if host == "first":
            raise TimeoutError("timed out")
        return QuoteData(np.asarray([] if host == "second" else [np.nan, np.inf]), 0)
    monkeypatch.setattr("app.live.fetch_quote_data", fetch)
    with pytest.raises(QuoteHostsError) as captured:
        fetch_quote_snapshot(HOSTS, 3)
    assert calls == ["first", "second", "third"]
    assert len(captured.value.attempts) == 3
    for host in HOSTS:
        endpoint = f"{host.host}:{host.port}"
        assert endpoint in str(captured.value)
        assert endpoint in describe_quote_error(captured.value)


def test_poller_only_publishes_successful_complete_snapshot(monkeypatch):
    """输入：首站失败、次站成功的一轮轮询；输出：仅一次成功回调；边界：故障切换中不报告错误帧。"""
    quotes, errors = [], []
    def on_quote(*args):
        """输入：完整快照；输出：无；用途：记录并结束测试；边界：不运行第二轮。"""
        quotes.append(args)
        poller.stop()
    poller = LivePoller(120, on_quote, errors.append, HOSTS, 3, min_stocks=1)
    def fetch(host, port, timeout, **kwargs):
        """输入：端点；输出：第二站涨幅；边界：第一站断连。"""
        if host == "first":
            raise TimeoutError("timed out")
        return QuoteData(np.asarray([-8.0, 2.0]), 2)
    monkeypatch.setattr("app.live.fetch_quote_data", fetch)
    poller._loop()
    assert len(quotes) == 1 and quotes[0][1] == 2
    assert not errors


def test_complete_raw_snapshot_with_no_valid_trades_succeeds(monkeypatch):
    """输入：原始股票完整但全部未成交；输出：零有效成功帧；边界：不换站、不报连接失败。"""
    calls = []
    def fetch(host, port, timeout, **kwargs):
        """输入：端点；输出：无有效成交的完整原始帧；边界：覆盖校验独立于过滤结果。"""
        calls.append(host)
        return QuoteData(np.asarray([]), 5578)
    monkeypatch.setattr("app.live.fetch_quote_data", fetch)
    snapshot = fetch_quote_snapshot(HOSTS, 3)
    assert calls == ["first"]
    assert snapshot.universe == 0 and snapshot.raw_stocks == 5578
    assert snapshot.counts == [0] * 11


@pytest.mark.parametrize("previous,first_raw", [(0, 1), (6000, 5300)])
def test_raw_minimum_and_previous_coverage_force_failover(monkeypatch, previous, first_raw):
    """输入：低于最少数量或上轮90%的首站；输出：切换完整备用站；边界：只比较原始股票数。"""
    calls = []
    def fetch(host, port, timeout, **kwargs):
        """输入：端点；输出：首站少量、备用完整；边界：有效数量均为1不影响覆盖判断。"""
        calls.append(host)
        return QuoteData(np.asarray([-7.]), first_raw if host == "first" else 5500)
    monkeypatch.setattr("app.live.fetch_quote_data", fetch)
    snapshot = fetch_quote_snapshot(HOSTS, 3, previous_raw_stocks=previous)
    assert snapshot.host == HOSTS[1] and calls == ["first", "second"]


def test_stop_during_fetch_suppresses_all_later_callbacks(monkeypatch):
    """输入：请求中停止；输出：无迟到成功/失败/刷新通知；边界：取消后线程有界退出。"""
    import threading
    entered, release = threading.Event(), threading.Event()
    callbacks = []
    def fetch(*args, **kwargs):
        """输入：轮询请求；输出：迟到快照；边界：模拟底层尚未响应取消。"""
        entered.set()
        release.wait(1)
        return Snapshot([1] * 11, 1, 5000, HOSTS[0])
    monkeypatch.setattr("app.live.fetch_quote_snapshot", fetch)
    poller = LivePoller(300, lambda *args: callbacks.append("quote"), lambda exc: callbacks.append("error"),
                        HOSTS, 1, on_refreshing=lambda value: callbacks.append(value))
    poller.start()
    assert entered.wait(1)
    poller.stop()
    before = list(callbacks)
    release.set()
    poller._thread.join(1)
    assert not poller._thread.is_alive() and callbacks == before


def test_manual_refresh_wakes_wait_and_never_runs_parallel(monkeypatch):
    """输入：等待中手动刷新；输出：立即串行第二轮；边界：300秒定时等待不影响重试。"""
    import threading
    first, done = threading.Event(), threading.Event()
    active, maximum, calls = 0, 0, []
    def fetch(*args, **kwargs):
        """输入：轮询请求；输出：完整帧；边界：记录并发峰值。"""
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        calls.append("fetch")
        active -= 1
        return Snapshot([1] * 11, 1, 5000, HOSTS[0])
    def quote(*args):
        """输入：成功帧；输出：无；用途：记录第一轮并在第二轮结束；边界：不使用真实等待。"""
        if len(calls) == 1:
            first.set()
        else:
            poller.stop()
            done.set()
    monkeypatch.setattr("app.live.fetch_quote_snapshot", fetch)
    poller = LivePoller(300, quote, lambda exc: None, HOSTS, 1,
                        on_refreshing=lambda active: (_ for _ in ()).throw(RuntimeError("UI unavailable")))
    poller.start()
    assert first.wait(1)
    poller.request_refresh()
    assert done.wait(1)
    poller._thread.join(1)
    assert len(calls) == 2 and maximum == 1


def test_all_hosts_failure_preserves_last_snapshot_and_next_round_recovers(monkeypatch):
    """输入：整轮全失败后下一轮恢复；输出：旧帧保留直到成功；边界：错误回调异常不会结束轮询。"""
    errors, quotes = [], []
    calls = []
    previous = Snapshot([7] * 11, 5000, 5500, HOSTS[0])
    def fetch(*args, **kwargs):
        """输入：轮询参数；输出：恢复帧；边界：首轮模拟全失败。"""
        calls.append(kwargs)
        if len(calls) == 1:
            raise QuoteHostsError([(host, TimeoutError("offline")) for host in HOSTS])
        return Snapshot([2] * 11, 5000, 5500, HOSTS[0])
    def error(exc):
        """输入：全失败错误；输出：无；用途：验证旧帧并重试；边界：模拟UI异常。"""
        assert poller.last_snapshot is previous
        errors.append(exc)
        poller.request_refresh()
        raise RuntimeError("UI unavailable")
    def quote(*args):
        """输入：恢复帧；输出：无；用途：终止测试；边界：仅允许成功一帧。"""
        quotes.append(args)
        poller.stop()
    monkeypatch.setattr("app.live.fetch_quote_snapshot", fetch)
    poller = LivePoller(300, quote, error, HOSTS, 1)
    poller.last_snapshot = previous
    poller._loop()
    assert len(errors) == 1 and len(quotes) == 1
    assert poller.last_snapshot.counts == [2] * 11
    assert calls[1]["previous_raw_stocks"] == 5500
