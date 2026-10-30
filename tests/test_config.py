"""日期段、主题回写和比例。"""

from pathlib import Path

from app.config import load_config, parse_window, refresh_label, save_theme
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
        "quote_host: \"127.0.0.1\"\n"
        "quote_port: 7709\n"
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
