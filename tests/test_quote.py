"""标准行情涨幅解码。不连网。"""

from app.quote_client import change_pct_from_record, is_stock_row


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
    record = bytearray(b"\x01" + b"600000" + b"\x00\x00")
    record.extend(_varint(1000))
    record.extend(_varint(-50))
    pct = change_pct_from_record(bytes(record))
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
