"""行情完整性、真实本机分包与有界取消回归，不连接外部站点。"""

import socket
import struct
import threading
import time
import zlib

import pytest

import app.quote_client as q


def _response(packet, body, *, compressed=False):
    """输入：请求及正文；输出：对应响应帧；边界：压缩长度与解压长度分别编码。"""
    raw = zlib.compress(body) if compressed else body
    return (struct.pack("<I", 7654321) + bytes((28, packet[1], 4, 10)) + b"\0\0"
            + packet[10:12] + struct.pack("<HH", len(raw), len(body)) + raw)


@pytest.mark.parametrize("compressed", [False, True])
def test_exchange_accepts_fragmented_valid_frame(compressed):
    """输入：分片的合法压缩/原文帧；输出：原正文；边界：头和体均允许 TCP 分包。"""
    left, right = socket.socketpair()
    packet = q._page_packet(0, 80, 4)
    body = b"public-market-data" * 40
    def server():
        """输入输出：无；用途：逐片发送响应；边界：对端完成后关闭。"""
        with right:
            right.recv(1024)
            frame = _response(packet, body, compressed=compressed)
            for offset in range(0, len(frame), 3):
                right.sendall(frame[offset:offset + 3])
    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    with left:
        left.settimeout(1)
        assert q._exchange(left, packet) == body
    thread.join(1)


@pytest.mark.parametrize("offset", [0, 5, 10])
def test_wrong_magic_sequence_or_command_rejected(offset):
    """输入：错误标识/序号/命令；输出：协议错误；边界：即使正文长度合法也不接纳。"""
    left, right = socket.socketpair()
    packet = q._page_packet(0, 80, 4)
    frame = bytearray(_response(packet, b"\0\0\0\0"))
    frame[offset] ^= 1
    right.sendall(frame)
    with left, right:
        left.settimeout(1)
        with pytest.raises(RuntimeError, match="不匹配"):
            q._exchange(left, packet)


def test_drip_feed_cannot_extend_frame_deadline():
    """输入：每次间隔低于 socket 超时的慢滴；输出：整帧仍超时；边界：不会无限等待下一字节。"""
    left, right = socket.socketpair()
    packet = q._page_packet(0, 80, 4)
    def server():
        """输入输出：无；用途：模拟持续慢滴；边界：客户端退出即停止。"""
        with right:
            right.recv(1024)
            try:
                for byte in _response(packet, b"x" * 100):
                    right.sendall(bytes((byte,)))
                    time.sleep(.01)
            except OSError:
                pass
    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    started = time.monotonic()
    with left:
        left.settimeout(.08)
        with pytest.raises(TimeoutError):
            q._exchange(left, packet)
    thread.join(1)
    assert time.monotonic() - started < .6


def _fake_pages(monkeypatch, pages):
    """输入：偏移到页的映射；输出：无；用途：保留真实四线程调度；边界：缺省偏移为空终止页。"""
    class Session:
        """分页调度的无网络连接。"""
        def close(self):
            """输入输出：无；用途：模拟关闭；边界：可重复。"""
    monkeypatch.setattr(q, "_open_session", lambda *args, **kwargs: Session())
    monkeypatch.setattr(q, "_parse_page", lambda body: body)
    def exchange(sock, packet, **kwargs):
        """输入：请求；输出：合成页；边界：按偏移返回，独立于线程调度顺序。"""
        return pages.get(struct.unpack_from("<H", packet, 16)[0], q._Page([], 0, ()))
    monkeypatch.setattr(q, "_exchange", exchange)


def test_short_page_then_real_stock_is_rejected(monkeypatch):
    """输入：短页后仍有股票；输出：完整性错误；边界：即使短页自身合法也拒绝。"""
    _fake_pages(monkeypatch, {0: q._Page([-8.], 1, ((0, "000001"),)),
                             80: q._Page([-9.], 1, ((0, "000002"),))})
    with pytest.raises(RuntimeError, match="短页"):
        q.fetch_quote_data("fake", 7709, 1)


def test_real_server_out_of_range_placeholder_is_ignored(monkeypatch):
    """输入：真实站越界 395001 占位页；输出：尾页正常成功；边界：占位不是股票不冒充有效后页。"""
    # 2026-11-02 首站 offset5600 实抓：原始头00005000，80条同结构395001记录。
    record = bytes.fromhex("00333935303031000000989e12000000ad8ba70e008decbe8f0392ced81ab5a3d9520000ac92b92b000000000002000000000080636e4f000000000000000000000000f3da7a3f000000000000000000000000000000000000000000000000000000000000")
    placeholder = q._parse_page(bytes.fromhex("00005000") + record * 80)
    assert placeholder.count == 80 and placeholder.keys == ((0, "395001"),) * 80
    _fake_pages(monkeypatch, {0: q._Page([], 1, ((0, "000001"),)),
                             80: placeholder})
    data = q.fetch_quote_data("fake", 7709, 1)
    assert data.raw_stocks == 1 and data.pcts.size == 0


@pytest.mark.parametrize("cross_page", [False, True])
def test_duplicate_stock_is_rejected_even_when_filtered(monkeypatch, cross_page):
    """输入：过滤无效股票在同页或跨页重复；输出：完整性错误；边界：不依赖有效涨幅数组判断重复。"""
    keys = tuple((0, f"000{i:03d}") for i in range(80))
    pages = ({0: q._Page([], 80, keys), 80: q._Page([], 1, (keys[0],))} if cross_page
             else {0: q._Page([], 2, (keys[0], keys[0]))})
    _fake_pages(monkeypatch, pages)
    with pytest.raises(RuntimeError, match="重复"):
        q.fetch_quote_data("fake", 7709, 1)


@pytest.mark.parametrize("cancelled", [False, True])
def test_stalled_real_sockets_are_closed_on_deadline_or_stop(monkeypatch, cancelled):
    """输入：四条本机连接停在握手；输出：有界退出且连接关闭；边界：取消不转成功回调。"""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    address = listener.getsockname()
    accepted, finished = [], threading.Event()
    def server():
        """输入输出：无；用途：接受四条连接不回包；边界：收尾关闭全部服务端连接。"""
        try:
            for _ in range(4):
                conn, _ = listener.accept()
                accepted.append(conn)
            finished.wait(2)
        finally:
            for conn in accepted:
                conn.close()
    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    cancel = threading.Event()
    timer = threading.Timer(.08, cancel.set) if cancelled else None
    if timer:
        timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(q.QuoteCancelled if cancelled else TimeoutError):
            q.fetch_quote_data(*address, 1, deadline_seconds=.15, cancel=cancel)
        assert time.monotonic() - started < .7
        assert len(accepted) == 4
        for conn in accepted:
            conn.settimeout(.3)
            while conn.recv(1024):
                pass
    finally:
        finished.set()
        listener.close()
        thread.join(1)


def test_slow_dns_does_not_extend_caller_deadline_or_open_late_socket(monkeypatch):
    """输入：阻塞DNS解析；输出：调用方按总时限退出；边界：迟到解析完成后不得再创建连接。"""
    from unittest.mock import Mock
    release = threading.Event()
    finished = []
    def resolve(*args, **kwargs):
        """输入：地址；输出：迟到解析结果；用途：模拟系统DNS阻塞；边界：取消后结果只被丢弃。"""
        release.wait(1)
        finished.append(True)
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 1))]
    create = Mock()
    monkeypatch.setattr(q.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(q.socket, "socket", create)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            q.fetch_quote_data("slow-dns", 7709, 1, deadline_seconds=.06)
        assert time.monotonic() - started < .5
    finally:
        release.set()
        finish = time.monotonic() + .5
        while len(finished) < 4 and time.monotonic() < finish:
            time.sleep(.01)
    assert len(finished) == 4
    create.assert_not_called()
