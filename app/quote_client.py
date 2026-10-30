"""标准行情实时涨幅。

只连接配置里的一台标准行情服务器。4 条连接同时向这台服务器要页，短页出现后结束。
不探测地址池，不连接扩展行情。
"""

from __future__ import annotations

import os
import socket
import struct
import threading
import zlib

import numpy as np

_PAGE_CAP = 80
_WORKERS = 4
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


def fetch_change_pcts(host: str, port: int, timeout: float, page_size: int = _PAGE_CAP) -> np.ndarray:
    """输入：标准行情地址、端口、超时秒数、每页条数。
    输出：股票涨幅百分点的一维数组。昨收无效的行不进入结果。
    用途：4 条连接同时向这一台服务器分页取涨幅。
    边界：只连这一台；页长超过 80 时按 80。任一连接失败或短页前缺页时抛出异常。
    """
    size = max(1, min(int(page_size), _PAGE_CAP))
    pages = _fetch_pages(host, int(port), float(timeout), size, _WORKERS)
    values: list[float] = []
    for start in sorted(pages):
        values.extend(pages[start][0])
    if not values:
        return np.empty(0, dtype=np.float64)
    return np.asarray(values, dtype=np.float64)


def _fetch_pages(
    host: str,
    port: int,
    timeout: float,
    page_size: int,
    workers: int,
) -> dict[int, tuple[list[float], int]]:
    """输入：地址、端口、超时、页长、连接数。
    输出：页偏移到（涨幅列表，声明条数）的映射，只保留短页及其之前的页。
    用途：4 条连接先全部连上，再从同一台服务器领页。
    边界：连接数不足、没有短页或短页前缺页时抛出 RuntimeError。
    """
    lock = threading.Lock()
    next_start = 0
    stop_at: int | None = None
    failed = False
    pages: dict[int, tuple[list[float], int]] = {}
    errors: list[str] = []
    connect_count = 0
    barrier = threading.Barrier(workers)

    def take() -> int | None:
        """输入：无。输出：下一页偏移；该停时为空。用途：给空闲连接分配页。边界：调用方须持有锁。"""
        nonlocal next_start
        if failed:
            return None
        if stop_at is not None and next_start > stop_at:
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
            sock = _open_session(host, port, timeout)
            with lock:
                connect_count += 1
            barrier.wait(timeout=20)
            waited = True
            if connect_count != workers:
                return
            seq = 4
            while True:
                with lock:
                    start = take()
                if start is None:
                    return
                body = _exchange(sock, _page_packet(start, page_size, seq))
                seq = (seq + 1) & 0xFF
                pcts, count = _page_change_pcts(body)
                with lock:
                    pages[start] = (pcts, count)
                    if count < page_size and (stop_at is None or start < stop_at):
                        stop_at = start
        except threading.BrokenBarrierError:
            return
        except Exception as exc:
            with lock:
                failed = True
                errors.append(str(exc))
            if not waited:
                try:
                    barrier.abort()
                except Exception:
                    pass
        finally:
            if sock is not None:
                sock.close()

    threads = [
        threading.Thread(target=worker, name=f"quote-{index}")
        for index in range(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if connect_count != workers:
        detail = errors[0] if errors else f"只连上 {connect_count}/{workers} 条"
        raise RuntimeError(f"行情连接失败: {detail}")
    if failed and stop_at is None:
        raise RuntimeError(errors[0] if errors else "行情分页失败")
    if stop_at is None:
        raise RuntimeError(errors[0] if errors else "行情分页没有结束")
    kept: dict[int, tuple[list[float], int]] = {}
    for start in range(0, stop_at + page_size, page_size):
        page = pages.get(start)
        if page is None:
            raise RuntimeError(f"缺少行情页 {start}")
        if start != stop_at and page[1] != page_size:
            raise RuntimeError(f"行情满页条数异常: start={start}, count={page[1]}")
        kept[start] = page
    return kept


def _open_session(host: str, port: int, timeout: float) -> socket.socket:
    """输入：地址、端口、超时秒数。
    输出：已完成握手的套接字。
    用途：建立一条只连这一台服务器的行情连接。
    边界：握手失败时关闭套接字并抛出异常。
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(timeout)
        _exchange(sock, _SETUP1_PREFIX + os.urandom(8))
        _exchange(sock, _SETUP2)
        _exchange(sock, _SETUP3)
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
    输出：涨幅百分点；不是股票或昨收无效时为空。
    用途：只解码现价和昨收。
    边界：记录短于市场号加代码时返回空。价格按股票 2 位小数。
    """
    if len(record) < 9 or record[0] not in (0, 1, 2):
        return None
    market = record[0]
    code = record[1:7].decode("ascii", errors="replace")
    if not is_stock_row(market, code):
        return None
    price, pos = _read_varint(record, 9)
    last_diff, _ = _read_varint(record, pos)
    last = (price + last_diff) / 100.0
    if last <= 0:
        return None
    return (price / 100.0 - last) / last * 100.0


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


def _exchange(sock: socket.socket, packet: bytes) -> bytes:
    """输入：已连接套接字和请求包。
    输出：解压后的响应正文。
    用途：发送一帧并读回一帧。
    边界：对端提前关闭或压缩长度不对时抛出 RuntimeError。
    """
    sock.sendall(packet)
    head = _recv_exact(sock, 16)
    _prefix, _seq, _cmd, zipsize, unzipsize = struct.unpack("<IIIHH", head)
    if zipsize <= 0:
        raise RuntimeError("行情响应正文长度为 0")
    raw = _recv_exact(sock, int(zipsize))
    if zipsize == unzipsize:
        body = raw
    else:
        decoder = zlib.decompressobj()
        body = decoder.decompress(raw, int(unzipsize) + 1)
        if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            raise RuntimeError("行情响应压缩不完整")
    if len(body) != int(unzipsize):
        raise RuntimeError("行情响应解压长度与头部不一致")
    return body


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    """输入：套接字和字节数。
    输出：恰好这么多字节。
    用途：避免短读把后半包留给下一次请求。
    边界：对端关闭时抛出 RuntimeError。
    """
    parts = []
    got = 0
    while got < size:
        chunk = sock.recv(size - got)
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
    if len(body) < 4:
        raise RuntimeError("行情分页正文过短")
    _unknown, count = struct.unpack_from("<HH", body, 0)
    if count <= 0:
        return [], 0
    starts = _record_starts(body, count)
    if len(starts) != count:
        raise RuntimeError(f"行情分页条数不符: 声明 {count}，定位 {len(starts)}")
    values: list[float] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(body)
        pct = change_pct_from_record(body[start:end])
        if pct is not None:
            values.append(pct)
    return values, int(count)


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
    用途：解码现价和昨收差。
    边界：缓冲区不够时抛出 IndexError。
    """
    value_byte = data[pos]
    number = value_byte & 0x3F
    negative = bool(value_byte & 0x40)
    shift = 6
    while value_byte & 0x80:
        pos += 1
        value_byte = data[pos]
        number += (value_byte & 0x7F) << shift
        shift += 7
    pos += 1
    if negative:
        number = -number
    return number, pos
