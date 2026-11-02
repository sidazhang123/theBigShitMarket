"""标准行情涨幅解码。不连网。"""

import struct
import math

import pytest

from app.config import QuoteHost
from app.live import fetch_quote_snapshot
from app.quote_client import _Page, _page_change_pcts, change_pct_from_record, is_stock_row


def test_stock_filter_matches_market_prefixes():
    """沪深京股票留下，指数和未知市场去掉。"""
    assert is_stock_row(1, "600000")
    assert is_stock_row(0, "300001")
    assert is_stock_row(2, "920001")
    assert not is_stock_row(1, "000001")
    assert not is_stock_row(0, "399001")
    assert not is_stock_row(1, "999999")


def test_change_pct_uses_price_and_previous_close():
    """现价 10.00、昨收差 -50（即昨收 9.50）时，涨幅约为 5.2632。"""
    pct = change_pct_from_record(_record(1000, -50, 100))
    assert pct is not None
    assert abs(pct - ((10.0 - 9.5) / 9.5 * 100)) < 1e-9


def _varint(number: int) -> bytes:
    """输入：有符号整数。
    输出：协议变长整数。
    用途：给解码测试造现价和昨收差。
    边界：只覆盖测试用的小整数。
    """
    negative = number < 0
    value = -number if negative else number
    first = value & 0x3F
    if negative:
        first |= 0x40
    rest = value >> 6
    if rest:
        first |= 0x80
    out = bytearray([first])
    while rest:
        piece = rest & 0x7F
        rest >>= 7
        if rest:
            piece |= 0x80
        out.append(piece)
    return bytes(out)


def _record(price: int, last_diff: int, volume: int, code: str = "600000") -> bytes:
    """输入：整数现价、昨收差、成交量和代码；输出：最少八字段记录；用途：合成报价；边界：不补第九字段。"""
    header = b"\x01" + code.encode("ascii") + b"\x00\x00"
    return header + b"".join(_varint(value) for value in (price, last_diff, 17, -23, -31, 93123456, 0, volume))


@pytest.mark.parametrize("price,last_diff,volume", [
    (0, 1000, 100), (-100, 1100, 100),
    (1000, -1000, 100), (1000, -1100, 100),
    (1000, 0, 0), (1000, 0, -1),
])
def test_invalid_price_previous_close_or_volume_is_excluded(price, last_diff, volume):
    """输入：非正现价、昨收或总成交量；输出：None；用途：排除占位和异常报价；边界：昨收由差值还原。"""
    assert change_pct_from_record(_record(price, last_diff, volume)) is None


@pytest.mark.parametrize("price,last_diff,volume", [
    (1000, 0, 1), (1000, -900, 100), (1, 999999, 100),
    (10**15, -(10**15) + 1, 10**10),
])
def test_valid_extreme_prices_changes_and_large_volume_are_kept(price, last_diff, volume):
    """输入：合法极端涨跌及大额成交量；输出：有限涨幅；用途：验证不设业务幅度或价格上限；边界：八字段足够。"""
    change = change_pct_from_record(_record(price, last_diff, volume))
    assert change is not None and math.isfinite(change)
    assert change == pytest.approx((price / (price + last_diff) - 1) * 100)


def test_truncated_or_unterminated_integer_records_are_excluded():
    """输入：任意位置截断及无终止整数；输出：None；用途：坏记录不破坏整页；边界：涵盖第八成交量字段。"""
    record = _record(1000, -50, 1000)
    for length in range(len(record)):
        assert change_pct_from_record(record[:length]) is None
    header = record[:9]
    assert change_pct_from_record(header + b"\x80" * 10 + b"\x00") is None
    assert change_pct_from_record(header + b"\x80" * 1000) is None
    fields = b"".join(_varint(value) for value in (1000, 0, 0, 0, 0, 0, 0))
    assert change_pct_from_record(header + fields + b"\x80") is None
    assert change_pct_from_record(header + fields + b"\x80" * 10 + b"\x00") is None


@pytest.mark.parametrize("price", [float("nan"), float("inf"), 10**1000])
def test_nonfinite_or_unconvertible_decoded_price_is_excluded(monkeypatch, price):
    """输入：异常解码值；输出：None；用途：防御浮点非有限和转换溢出；边界：只替换解码器，不走真实网络。"""
    values = iter((price, 0, 0, 0, 0, 0, 0, 1))
    monkeypatch.setattr("app.quote_client._read_varint", lambda data, pos: (next(values), pos + 1))
    assert change_pct_from_record(b"\x01" + b"600000" + b"\x00\x00" + b"\x00" * 8) is None


def test_mixed_page_filters_records_without_changing_declared_page_count():
    """输入：正常、零量、异常价格和截断字段混页；输出：仅有效涨幅与原声明数；边界：翻页终点不受过滤影响。"""
    valid = _record(900, 100, 100, "600001").ljust(80, b"\x00")
    zero_volume = _record(900, 100, 0, "600002").ljust(80, b"\x00")
    zero_price = _record(0, 1000, 100, "600003").ljust(80, b"\x00")
    malformed = b"\x01" + b"600004" + b"\x00\x00" + b"\x80" * 71
    values, count = _page_change_pcts(struct.pack("<HH", 0, 4) + valid + zero_volume + zero_price + malformed)
    assert count == 4
    assert values == pytest.approx([-10.0])


@pytest.mark.parametrize("code,record_hex", [
    ("000831", "00303030383331000000974a000000a1b2e30a0000000000000000000000000000002080000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    ("002129", "00303032313239000000aa0e000000a1b2e30a0000000000000000000000000000002080000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    ("002717", "003030323731370000002d000000a1b2e30a00000000000000000000000000000020c0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    ("001381", "0030303133383100000000000000a4010000000000000000000000000000000080000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    ("601326", "01363031333236000000a505000000b1cbeb080000000000000000000000000000002080000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    ("688121", "0136383831323100000028000000b1cbeb0800000000000000000000000000000020c0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    ("688496", "013638383439360000003a000000b1cbeb0800000000000000000000000000000020c0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    ("688827", "0136383838323700000000000000a4010000000000000000000000000000000080000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
    ("920085", "0239323030383500000000000000a4010000000000000000000000000000000080000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"),
])
def test_captured_inactive_and_zero_price_quotes_are_excluded(code, record_hex):
    """输入：2026-11-02实际捕获的九条异常记录；输出：None；用途：回归排除停牌占位；边界：fixture不依赖网络或build。"""
    record = bytes.fromhex(record_hex)
    assert record[1:7].decode("ascii") == code
    assert change_pct_from_record(record) is None


def test_partial_pages_are_discarded_before_next_four_connection_host(monkeypatch):
    """输入：首站中途缺页和备用站完整分页；输出：只含备用站家数；边界：四连接同站且全部关闭。"""
    opened, closed = [], []

    class Session:
        """只记录端点的假连接，分页调度仍使用真实四线程。"""

        def __init__(self, host):
            """输入：端点；输出：假连接；边界：不建 TCP。"""
            self.host = host

        def close(self):
            """输入输出：无；用途：记录资源释放；边界：每条连接只关闭一次。"""
            closed.append(self.host)

    def open_session(host, port, timeout, **kwargs):
        """输入：端点和超时；输出：假连接；用途：记录同站连接数；边界：握手直接成功。"""
        opened.append(host)
        return Session(host)

    def exchange(session, packet, **kwargs):
        """输入：分页请求；输出：模拟正文；用途：首站第二页失败；边界：备用站第二页为短页。"""
        start = struct.unpack_from("<H", packet, 16)[0]
        if session.host == "first" and start == 80:
            raise RuntimeError("缺少行情页 80")
        if start == 0:
            return _Page([-99.0] if session.host == "first" else [-7.0], 80,
                         tuple((0, f"000{i:03d}") for i in range(80)))
        return _Page([-8.0], 1, ((0, "000999"),)) if start == 80 else _Page([], 0, ())

    monkeypatch.setattr("app.quote_client._open_session", open_session)
    monkeypatch.setattr("app.quote_client._exchange", exchange)
    monkeypatch.setattr("app.quote_client._parse_page", lambda body: body)
    snapshot = fetch_quote_snapshot((QuoteHost("first", 7709), QuoteHost("second", 7709)), 3, min_stocks=1)
    assert opened == ["first"] * 4 + ["second"] * 4
    assert sorted(closed) == sorted(opened)
    assert snapshot.universe == 2 and snapshot.counts[0] == 0 and snapshot.counts[-1] == 2
