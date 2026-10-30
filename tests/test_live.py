"""行情故障文案。不连网。"""

import socket
import struct
import zlib

from app.live import describe_quote_error


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
