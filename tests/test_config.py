"""日期段、主题回写和比例。"""

from pathlib import Path

import pytest

from app.config import QuoteHost, load_config, parse_window, refresh_label, save_refresh_seconds, save_theme
from app.scale import half_px


def test_parse_windows():
    """短结束日、跨年和完整结束日都能解析。多写一位的起始日拒绝。"""
    assert parse_window("20260717-0730") == (
        __import__("datetime").date(2026, 7, 17),
        __import__("datetime").date(2026, 7, 30),
    )
    assert parse_window("20250103-0113")[1].day == 13
    assert parse_window("20240816-20240923")[1] == __import__("datetime").date(2024, 9, 23)
    assert parse_window("20241220-0105") == (
        __import__("datetime").date(2024, 12, 20),
        __import__("datetime").date(2025, 1, 5),
    )
    try:
        parse_window("202501033-0113")
    except ValueError:
        pass
    else:
        raise AssertionError("多出来的一位应该被拒绝")


def test_refresh_label():
    """整分钟用 min，其余用秒。"""
    assert refresh_label(120) == "2 min 刷新"
    assert refresh_label(90) == "90 秒刷新"


def test_half_px_inset_once_and_zero_base():
    """半宽按 >6% 基底缩放。内缩只影响传入的系数。基底为 0 时不除零。"""
    assert half_px(5043, 5043, 300, 1) == 300
    assert half_px(5043, 5043, 300, 0.9) == 270
    assert half_px(10, 0, 300, 0.9) == 0
    assert half_px(0, 100, 300, 1) == 0


def test_save_theme_rewrites_only_theme_line(tmp_path: Path):
    """切换主题只改 theme 行，日期段保持原样。"""
    path = tmp_path / "config.yaml"
    path.write_text(
        "source_db: \"D:\\\\图形量价选股\\\\quant.duckdb\"\n"
        "refresh_seconds: 120\n"
        "min_codes: 4500\n"
        "half_width_px: 300\n"
        "contour_inset: 0.9\n"
        "formula_version: 1\n"
        "theme: dark\n"
        "quote_hosts:\n  - host: \"127.0.0.1\"\n    port: 7709\n"
        "quote_timeout: 8\n"
        "crash_windows:\n"
        "  - \"20250103-0113\"\n",
        encoding="utf-8",
    )
    config = load_config(path)
    updated = save_theme(config, "light")
    text = path.read_text(encoding="utf-8")
    assert updated.theme == "light"
    assert "theme: light" in text
    assert "20250103-0113" in text
    assert load_config(path).theme == "light"


def test_refresh_choices_persist_and_preserve_other_settings(tmp_path: Path):
    """输入：各下拉秒数；输出：配置同步；边界：保留日期、注释、主题并拒绝非法值。"""
    from dataclasses import replace

    path = tmp_path / "config.yaml"
    original = 'theme: dark\nrefresh_seconds: 120  # 刷新周期\nquote_hosts:\n  - host: localhost\ncrash_windows:\n  - "20250103-0113"\n'
    path.write_text(original, encoding="utf-8")
    config = replace(load_config(path), config_path=path)
    for seconds in (60, 120, 180, 240, 300):
        config = save_refresh_seconds(config, seconds)
        assert load_config(path).refresh_seconds == seconds
        assert "# 刷新周期" in path.read_text(encoding="utf-8")
    config = save_theme(config, "light")
    assert config.refresh_seconds == 300
    assert load_config(path).theme == "light"
    assert '"20250103-0113"' in path.read_text(encoding="utf-8")
    before = path.read_bytes()
    for bad in (0, 90, 360, True, 120.0, "120"):
        with pytest.raises(ValueError):
            save_refresh_seconds(config, bad)
        assert path.read_bytes() == before


def test_refresh_setting_can_be_missing(tmp_path: Path):
    """输入：缺省刷新键；输出：补齐所选周期；边界：其它配置保持。"""
    path = tmp_path / "config.yaml"
    path.write_text('quote_hosts:\n  - host: localhost\ncrash_windows:\n  - "20250103-0113"\n', encoding="utf-8")
    config = load_config(path)
    assert config.refresh_seconds == 120
    save_refresh_seconds(config, 180)
    assert load_config(path).refresh_seconds == 180


def test_quote_hosts_keep_priority_and_default_port(tmp_path: Path):
    """输入：有序多端点和重复项；输出：保序去重的不可变配置；边界：端口缺省为 7709。"""
    path = tmp_path / "config.yaml"
    path.write_text(
        'quote_hosts:\n  - host: " first "\n    port: 7710\n  - host: second\n  - host: first\n    port: 7710\n'
        'quote_timeout: 3.5\ncrash_windows:\n  - "20250103-0113"\n', encoding="utf-8",
    )
    config = load_config(path)
    assert config.quote_hosts == (QuoteHost("first", 7710), QuoteHost("second", 7709))
    assert config.quote_timeout == 3.5
    save_theme(config, "light")
    assert load_config(path).quote_hosts == config.quote_hosts


@pytest.mark.parametrize("hosts", [
    "null", "[]", '"first:7709"', "[first]", "[{}]", '[{host: ""}]',
    '[{host: "two words"}]', "[{host: first, port: 0}]", "[{host: first, port: 65536}]",
    "[{host: first, port: true}]", "[{host: first, port: 7709.5}]",
])
def test_invalid_quote_hosts_rejected(tmp_path: Path, hosts: str):
    """输入：空或非法端点列表；输出：配置错误；用途：启动前校验；边界：不发起连接。"""
    path = tmp_path / "config.yaml"
    path.write_text(f'quote_hosts: {hosts}\ncrash_windows:\n  - "20250103-0113"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="quote_hosts"):
        load_config(path)


@pytest.mark.parametrize("timeout", ["0", "-1", ".nan", ".inf"])
def test_invalid_quote_timeout_rejected(tmp_path: Path, timeout: str):
    """输入：无效超时；输出：配置错误；用途：保证故障切换有界；边界：非有限值拒绝。"""
    path = tmp_path / "config.yaml"
    path.write_text(f'quote_hosts: [{{host: first}}]\nquote_timeout: {timeout}\ncrash_windows:\n  - "20250103-0113"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="quote_timeout"):
        load_config(path)


@pytest.mark.parametrize("text", [
    'crash_windows: ["20250103-0113", "20250103-20250113"]\n',
    'crash_windows: ["20250103-0113", "20250103-0113"]\n',
])
def test_duplicate_normalized_windows_rejected(tmp_path, text):
    """输入：相同日期范围不同写法；输出：加载错误；用途：防止历史JOIN放大；边界：不连历史库。"""
    path = tmp_path / "config.yaml"
    path.write_text('quote_hosts: [{host: first}]\n' + text, encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_config(path)


@pytest.mark.parametrize("setting", [
    "quote_deadline_seconds: 0", "quote_deadline_seconds: .inf", "quote_min_stocks: true",
    "quote_min_stocks: 0", "quote_min_coverage: 1.1", "quote_min_coverage: .nan",
    "half_width_px: -1", "contour_inset: 0", "refresh_seconds: true", "min_codes: 1.5",
    "theme: dark\ntheme: light",
])
def test_invalid_protection_and_duplicate_keys_rejected(tmp_path, setting):
    """输入：无效保护参数及重复设置；输出：清晰配置错误；边界：不创建窗口或请求。"""
    path = tmp_path / "config.yaml"
    path.write_text('quote_hosts: [{host: first}]\ncrash_windows: ["20250103-0113"]\n' + setting + '\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_settings_update_supports_quoted_keys_and_inline_mapping(tmp_path):
    """输入：合法带引号键或行内映射；输出：设置正确保存；边界：不追加重复键。"""
    path = tmp_path / "config.yaml"
    for text in [
        '"theme": dark\nquote_hosts: [{host: first}]\ncrash_windows: ["20250103-0113"]\n',
        '{theme: dark, quote_hosts: [{host: first}], crash_windows: ["20250103-0113"]}',
    ]:
        path.write_text(text, encoding="utf-8")
        config = load_config(path)
        save_theme(config, "light")
        assert load_config(path).theme == "light"
        save_refresh_seconds(config, 180)
        assert load_config(path).refresh_seconds == 180
