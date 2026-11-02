"""标准行情实时涨幅。

单次抓取只连接一台标准行情服务器。4 条连接同时向这台服务器要页，短页出现后结束。
有序故障切换由实时轮询层负责；本模块不探测地址池，不连接扩展行情。
"""

from __future__ import annotations

import math
import os
import socket
import struct
import threading
import time
import zlib
from dataclasses import dataclass

import numpy as np

_PAGE_CAP = 80
_WORKERS = 4
_MAX_VARINT_BYTES = 10
_SETUP1_PREFIX = bytes.fromhex(
    "0c01187b00011a011a010b0047d7b5ff89a775cb6f9a279b25c23750749933ae"
    "27700357749933ae27700357749933ae27700357749933ae27700357749933ae"
    "27700357749933ae27700357749933ae27700357749933ae27700357749933ae"
    "27700357749933ae277003571ca34012266752b310928f2dbc306c776b5bc87e"
    "b3376a05588b8447c7bbb2528608c62da95a2b69749933ae27700357749933ae"
    "27700357749933ae27700357749933ae27700357749933ae27700357749933ae"
    "27700357f7e5f2e0de0975886f9a279b25c23750749933ae27700357749933ae"
    "27700357749933ae27700357749933ae27700357749933ae27700357749933ae"
    "27700357749933ae27700357749933ae27700357749933ae27700357"
)
_SETUP2 = bytes.fromhex("0c0218940001030003000d0002")
_SETUP3 = bytes.fromhex(
    "0c031899000120002000db0fd2f8bad3d6a4c8af"
    "0000007b143a410e000000000000000000000000"
    "0005"
)


@dataclass(frozen=True)
class QuoteData:
    """单站完整行情：过滤后的涨幅与过滤前唯一股票数分开保存。"""

    pcts: np.ndarray
    raw_stocks: int


@dataclass(frozen=True)
class _Page:
    """页声明条数、全部证券标识及有效成交涨幅。"""

    values: list[float]
    count: int
    keys: tuple[tuple[int, str], ...]


class QuoteCancelled(RuntimeError):
    """请求已取消，不应触发行情成功或失败回调。"""


class _Request:
    """单站总时限与所有连接的生命周期，取消时关闭连接以中断 IO。"""

    def __init__(self, timeout: float, seconds: float, cancel: threading.Event | None = None):
        """输入：操作超时、单站时限、停止事件；输出：请求上下文；边界：时限使用单调时钟。"""
        self.timeout = timeout
        self.deadline = time.monotonic() + seconds
        self.cancel = cancel
        self.aborted = threading.Event()
        self.lock = threading.Lock()
        self.sockets: set[socket.socket] = set()

    def check(self) -> None:
        """输入输出：无；用途：检查取消和单站时限；边界：到期抛错，不继续分页。"""
        if self.cancel is not None and self.cancel.is_set():
            raise QuoteCancelled("行情请求已取消")
        if time.monotonic() >= self.deadline:
            raise TimeoutError("单站行情总时限已到")
        if self.aborted.is_set():
            raise QuoteCancelled("行情请求已结束")

    def add(self, sock: socket.socket) -> None:
        """输入：新连接；输出：无；用途：连接前登记以便取消；边界：已终止时立即关闭。"""
        with self.lock:
            try:
                self.check()
            except Exception:
                sock.close()
                raise
            self.sockets.add(sock)

    def close(self) -> None:
        """输入输出：无；用途：结束所有连接；边界：shutdown/close 可重复并忽略已关闭连接。"""
        self.aborted.set()
        with self.lock:
            sockets = tuple(self.sockets)
            self.sockets.clear()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()


def fetch_change_pcts(host: str, port: int, timeout: float, page_size: int = _PAGE_CAP) -> np.ndarray:
    """输入：单站地址和参数；输出：有效涨幅数组；用途：轻量调用；边界：仍校验完整分页与重复代码。"""
    return fetch_quote_data(host, port, timeout, page_size=page_size).pcts


def fetch_quote_data(host: str, port: int, timeout: float, *, page_size: int = _PAGE_CAP,
                     deadline_seconds: float = 30, cancel: threading.Event | None = None) -> QuoteData:
    """输入：单站地址、操作超时、单站时限及取消事件；输出：完整行情及原始股票数。
    用途：同站四连接抓取并校验跨页唯一性；边界：无有效成交可成功，原始完整性由上层覆盖阈值校验。
    """
    request = _Request(float(timeout), float(deadline_seconds), cancel)
    size = max(1, min(int(page_size), _PAGE_CAP))
    try:
        pages = _fetch_pages(host, int(port), float(timeout), size, _WORKERS, request=request)
        keys: set[tuple[int, str]] = set()
        values: list[float] = []
        for start in sorted(pages):
            page = pages[start]
            for key in page.keys:
                if key in keys:
                    raise RuntimeError(f"行情重复证券: {key[0]}:{key[1]}")
                keys.add(key)
            values.extend(page.values)
        raw_stocks = sum(is_stock_row(market, code) for market, code in keys)
        if cancel is not None and cancel.is_set():
            raise QuoteCancelled("行情请求已取消")
        if time.monotonic() >= request.deadline:
            raise TimeoutError("单站行情总时限已到")
        return QuoteData(np.asarray(values, dtype=np.float64), raw_stocks)
    finally:
        request.close()


def _fetch_pages(
    host: str,
    port: int,
    timeout: float,
    page_size: int,
    workers: int,
    *, request: _Request | None = None,
) -> dict[int, _Page]:
    """输入：地址、端口、超时、页长、连接数。
    输出：页偏移到解析页的映射，只保留短页及其之前的页。
    用途：4 条连接先全部连上，再从同一台服务器领页。
    边界：短页后再核验一页；非股票越界占位不构成后页。连接、完整性、超时或取消失败均有界退出。
    """
    request = request or _Request(timeout, 30)
    lock = threading.Lock()
    next_start = 0
    stop_at: int | None = None
    failed = False
    pages: dict[int, _Page] = {}
    errors: list[str] = []
    connect_count = 0
    barrier = threading.Barrier(workers)

    def take() -> int | None:
        """输入：无。输出：下一页偏移；该停时为空。用途：给空闲连接分配页。边界：调用方须持有锁。"""
        nonlocal next_start
        if failed:
            return None
        if stop_at is not None and next_start > stop_at + page_size:
            return None
        if next_start > 0xFFFF:
            return None
        start = next_start
        next_start += page_size
        return start

    def worker() -> None:
        """输入：无。输出：无。用途：一条连接反复领页直到短页。边界：失败时标记整段失败。"""
        nonlocal connect_count, stop_at, failed
        sock: socket.socket | None = None
        waited = False
        try:
            sock = _open_session(host, port, timeout, request=request)
            with lock:
                connect_count += 1
            barrier.wait(timeout=max(.001, request.deadline - time.monotonic()))
            waited = True
            if connect_count != workers:
                return
            seq = 4
            while True:
                with lock:
                    start = take()
                if start is None:
                    return
                request.check()
                body = _exchange(sock, _page_packet(start, page_size, seq), request=request)
                seq = (seq + 1) & 0xFF
                page = _parse_page(body)
                if page.count > page_size:
                    raise RuntimeError("行情页声明条数超过请求")
                with lock:
                    pages[start] = page
                    if page.count < page_size and (stop_at is None or start < stop_at):
                        stop_at = start
        except threading.BrokenBarrierError:
            return
        except Exception as exc:
            with lock:
                failed = True
                errors.append(str(exc))
            request.close()
            if not waited:
                try:
                    barrier.abort()
                except Exception:
                    pass
        finally:
            if sock is not None:
                sock.close()

    threads = [
        threading.Thread(target=worker, name=f"quote-{index}", daemon=True)
        for index in range(workers)
    ]
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            request.check()
            if failed:
                break
            for thread in threads:
                thread.join(timeout=min(.02, max(0, request.deadline - time.monotonic())))
    except QuoteCancelled:
        if not failed:
            raise
    finally:
        request.close()
        finish = time.monotonic() + .2
        for thread in threads:
            thread.join(timeout=max(0, finish - time.monotonic()))
    if request.cancel is not None and request.cancel.is_set():
        raise QuoteCancelled("行情请求已取消")
    if connect_count != workers:
        detail = errors[0] if errors else f"只连上 {connect_count}/{workers} 条"
        raise RuntimeError(f"行情连接失败: {detail}")
    if failed:
        raise RuntimeError(errors[0] if errors else "行情分页失败")
    if stop_at is None:
        raise RuntimeError(errors[0] if errors else "行情分页没有结束")
    # 真实站越界会返回重复的非股票 395001 占位；只有真实股票才构成尾页矛盾。
    if any(start > stop_at and any(is_stock_row(*key) for key in page.keys) for start, page in pages.items()):
        raise RuntimeError("短页之后仍有非空行情页")
    kept: dict[int, _Page] = {}
    for start in range(0, stop_at + page_size, page_size):
        page = pages.get(start)
        if page is None:
            raise RuntimeError(f"缺少行情页 {start}")
        if start != stop_at and page.count != page_size:
            raise RuntimeError(f"行情满页条数异常: start={start}, count={page.count}")
        kept[start] = page
    return kept


def _open_session(host: str, port: int, timeout: float, *, request: _Request | None = None) -> socket.socket:
    """输入：地址、端口、超时秒数。
    输出：已完成握手的套接字。
    用途：建立一条只连这一台服务器的行情连接。
    边界：握手失败时关闭套接字并抛出异常。
    """
    request = request or _Request(timeout, 30)
    sock = None
    last_error = None
    for family, socktype, proto, _, address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        request.check()
        sock = socket.socket(family, socktype, proto)
        request.add(sock)
        try:
            sock.settimeout(min(timeout, request.deadline - time.monotonic()))
            sock.connect(address)
            break
        except OSError as exc:
            last_error = exc
            sock.close()
            sock = None
    if sock is None:
        raise last_error or ConnectionError("无法解析行情地址")
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(timeout)
        _exchange(sock, _SETUP1_PREFIX + os.urandom(8), request=request)
        _exchange(sock, _SETUP2, request=request)
        _exchange(sock, _SETUP3, request=request)
    except Exception:
        sock.close()
        raise
    return sock


def is_stock_row(market: int, code: str) -> bool:
    """输入：市场号和 6 位代码。
    输出：是否计入全市场股票。
    用途：去掉指数和板块占位。
    边界：不读码表。市场号不是 0、1、2 时返回假。
    """
    text = str(code).strip()
    if len(text) != 6 or not text.isdigit():
        return False
    if text.startswith(("399", "880")) or text in {"999999", "999998", "999997"}:
        return False
    if market == 0:
        return text.startswith(("000", "001", "002", "003", "300", "301"))
    if market == 1:
        return text.startswith(("60", "68"))
    if market == 2:
        return text.startswith(("43", "83", "87", "88", "92"))
    return False


def change_pct_from_record(record: bytes) -> float | None:
    """输入：一条版面报价记录。
    输出：有效成交股票的涨幅百分点，其它记录为空。
    用途：解码现价、昨收及成交量，排除停牌占位和异常价格。
    边界：价格按两位小数；截断、过长整数及非有限数值只丢弃本记录，不限制合法涨跌幅。
    """
    if len(record) < 9 or record[0] not in (0, 1, 2):
        return None
    market = record[0]
    code = record[1:7].decode("ascii", errors="replace")
    if not is_stock_row(market, code):
        return None
    try:
        price, pos = _read_varint(record, 9)
        last_diff, pos = _read_varint(record, pos)
        # 昨收差之后依次为开盘差、最高差、最低差、时间和保留字段，随后是总成交量。
        for _ in range(5):
            _, pos = _read_varint(record, pos)
        volume, _ = _read_varint(record, pos)
        previous = price + last_diff
        if price <= 0 or previous <= 0 or volume <= 0:
            return None
        current = price / 100.0
        last = previous / 100.0
        if not math.isfinite(current) or not math.isfinite(last):
            return None
        change = (current - last) / last * 100.0
        return change if math.isfinite(change) else None
    except (IndexError, ValueError, OverflowError, ZeroDivisionError):
        return None


def _page_packet(start: int, count: int, seq: int) -> bytes:
    """输入：列表偏移、条数、包序号。
    输出：一页版面报价请求。
    用途：组分页请求。
    边界：条数钳制到 1..80。
    """
    count = max(1, min(int(count), _PAGE_CAP))
    header = struct.pack(
        "<HIHHH",
        0x000C | ((int(seq) & 0xFF) << 8),
        0x01000A04,
        20,
        20,
        0x054B,
    )
    body = struct.pack(
        "<HHHHHHHHH",
        6,
        0,
        int(start) & 0xFFFF,
        count,
        0,
        5,
        0,
        1,
        0,
    )
    return header + body


def _exchange(sock: socket.socket, packet: bytes, *, request: _Request | None = None) -> bytes:
    """输入：已连接套接字和请求包。
    输出：解压后的响应正文。
    用途：发送一帧并读回一帧。
    边界：响应标识、序号和命令必须匹配；头体共用整帧时限，提前关闭或压缩异常抛错。
    """
    request = request or _Request(sock.gettimeout() or 8, 30)
    request.check()
    frame_deadline = min(request.deadline, time.monotonic() + request.timeout)
    sock.settimeout(max(.001, frame_deadline - time.monotonic()))
    sock.sendall(packet)
    head = _recv_exact(sock, 16, request=request, deadline=frame_deadline)
    prefix, _seq, _cmd, zipsize, unzipsize = struct.unpack("<IIIHH", head)
    if prefix != 7654321 or head[5] != packet[1] or head[10:12] != packet[10:12]:
        raise RuntimeError("行情响应标识、序号或命令不匹配")
    if zipsize <= 0:
        raise RuntimeError("行情响应正文长度为 0")
    raw = _recv_exact(sock, int(zipsize), request=request, deadline=frame_deadline)
    if zipsize == unzipsize:
        body = raw
    else:
        decoder = zlib.decompressobj()
        body = decoder.decompress(raw, int(unzipsize) + 1)
        if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            raise RuntimeError("行情响应压缩不完整")
    if len(body) != int(unzipsize):
        raise RuntimeError("行情响应解压长度与头部不一致")
    sock.settimeout(request.timeout)
    return body


def _recv_exact(sock: socket.socket, size: int, *, request: _Request | None = None,
                deadline: float | None = None) -> bytes:
    """输入：套接字和字节数。
    输出：恰好这么多字节。
    用途：避免短读把后半包留给下一次请求。
    边界：逐片读取不重置时限；对端关闭、到期或取消时抛错。
    """
    request = request or _Request(sock.gettimeout() or 8, 30)
    deadline = min(request.deadline, deadline or time.monotonic() + request.timeout)
    parts = []
    got = 0
    while got < size:
        request.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("行情帧读取超时")
        sock.settimeout(min(.1, remaining))
        try:
            chunk = sock.recv(size - got)
        except socket.timeout:
            continue
        if not chunk:
            raise RuntimeError("行情连接已断开")
        parts.append(chunk)
        got += len(chunk)
    if len(parts) == 1:
        return parts[0]
    return b"".join(parts)


def _page_change_pcts(body: bytes) -> tuple[list[float], int]:
    """输入：一页解压后的版面正文。
    输出：本页股票涨幅，以及服务端声明的记录条数。
    用途：按声明条数切出记录再取涨幅。翻页终点看声明条数，不看过滤后的股票数。
    边界：声明 0 条时返回空列表和 0。条数对不上时抛出 RuntimeError。
    """
    page = _parse_page(body)
    return page.values, page.count


def _parse_page(body: bytes) -> _Page:
    """输入：解压页；输出：声明条数、所有证券标识和有效涨幅；边界：坏价量丢记录，无法定位整页抛错。"""
    if len(body) < 4:
        raise RuntimeError("行情分页正文过短")
    _unknown, count = struct.unpack_from("<HH", body, 0)
    if count <= 0:
        return _Page([], 0, ())
    starts = _record_starts(body, count)
    if len(starts) != count:
        raise RuntimeError(f"行情分页条数不符: 声明 {count}，定位 {len(starts)}")
    values: list[float] = []
    keys: list[tuple[int, str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(body)
        record = body[start:end]
        keys.append((record[0], record[1:7].decode("ascii")))
        pct = change_pct_from_record(record)
        if pct is not None:
            values.append(pct)
    return _Page(values, int(count), tuple(keys))


def _record_starts(body: bytes, count: int) -> list[int]:
    """输入：正文和期望条数。
    输出：每条记录的起始偏移。
    用途：按市场号加 6 位代码定位变长行。
    边界：相邻起点至少相隔 70 字节。找不到足够条数时返回已找到的部分。
    """
    starts: list[int] = []
    for offset in range(4, max(4, len(body) - 6)):
        if body[offset] not in (0, 1, 2):
            continue
        code = body[offset + 1 : offset + 7]
        if len(code) < 6 or not all(0x30 <= byte <= 0x39 for byte in code):
            continue
        if not starts or offset - starts[-1] >= 70:
            starts.append(offset)
            if len(starts) >= int(count):
                break
    return starts


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """输入：缓冲区和起点。
    输出：有符号整数，以及下一读取位置。
    用途：有界解码价格差、时间和成交量等整数字段。
    边界：截断或连续十字节仍未终止时抛出 ValueError，调用方决定丢记录或整页失败。
    """
    number = 0
    negative = False
    for index in range(_MAX_VARINT_BYTES):
        if pos < 0 or pos >= len(data):
            raise ValueError("行情变长整数截断")
        value_byte = data[pos]
        if index == 0:
            number = value_byte & 0x3F
            negative = bool(value_byte & 0x40)
        else:
            number |= (value_byte & 0x7F) << (6 + (index - 1) * 7)
        pos += 1
        if not value_byte & 0x80:
            return (-number if negative else number), pos
    raise ValueError("行情变长整数过长")
