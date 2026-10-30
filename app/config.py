"""读取和回写 config.yaml。

用户可调项只放在配置文件里。本模块不解析命令行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

from app.paths import cache_file, runtime_root

DEFAULT_CONFIG_PATH = runtime_root() / "config.yaml"

_THEME_LINE = re.compile(r"(?m)^theme:\s*\S+\s*$")


@dataclass(frozen=True)
class AppConfig:
    """运行期配置。路径和日期段在加载时已经规范化。"""

    source_db: str
    refresh_seconds: int
    min_codes: int
    half_width_px: float
    contour_inset: float
    formula_version: int
    theme: str
    quote_host: str
    quote_port: int
    quote_timeout: float
    crash_windows: tuple[str, ...]
    config_path: Path
    cache_path: Path


def parse_window(text: str) -> tuple[date, date]:
    """输入：YYYYMMDD-MMDD 或 YYYYMMDD-YYYYMMDD。
    输出：含首尾的开始日和结束日。
    用途：把配置里的股灾日期段变成日期。
    边界：短结束日的月日早于开始月日时，结束年加 1。空串、长度不对或非法日期抛出 ValueError。
    """
    raw = str(text).strip()
    if "-" not in raw:
        raise ValueError(f"无法解析日期段: {text}")
    left, right = raw.split("-", 1)
    if len(left) != 8 or not left.isdigit():
        raise ValueError(f"无法解析日期段: {text}")
    start = _parse_ymd(left)
    if len(right) == 8 and right.isdigit():
        end = _parse_ymd(right)
    elif len(right) == 4 and right.isdigit():
        month = int(right[:2])
        day = int(right[2:])
        year = start.year + 1 if (month, day) < (start.month, start.day) else start.year
        end = _make_date(year, month, day)
    else:
        raise ValueError(f"无法解析日期段: {text}")
    if end < start:
        raise ValueError(f"日期段结束早于开始: {text}")
    return start, end


def load_config(path: Path | None = None) -> AppConfig:
    """输入：配置文件路径；缺省为项目根目录的 config.yaml。
    输出：AppConfig。
    用途：启动和重算时读取全部可调参数。
    边界：缺少日期段、主题不是 dark/light、刷新秒数或覆盖阈值小于 1 时抛出 ValueError。行情库路径可空，启动不检查、不打开。
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    windows = tuple(str(item).strip() for item in data.get("crash_windows") or ())
    if not windows:
        raise ValueError("config.yaml 的 crash_windows 不能为空")
    for item in windows:
        parse_window(item)
    theme = str(data.get("theme") or "dark").strip().lower()
    if theme not in {"dark", "light"}:
        raise ValueError("theme 只能是 dark 或 light")
    refresh_seconds = int(data.get("refresh_seconds", 120))
    min_codes = int(data.get("min_codes", 4500))
    if refresh_seconds < 1 or min_codes < 1:
        raise ValueError("refresh_seconds 和 min_codes 必须大于 0")
    source_db = str(data.get("source_db") or "").strip()
    quote_host = str(data.get("quote_host") or "").strip()
    quote_port = int(data.get("quote_port", 7709))
    quote_timeout = float(data.get("quote_timeout", 8))
    if not quote_host or quote_port < 1 or quote_timeout <= 0:
        raise ValueError("quote_host、quote_port、quote_timeout 必须有效")
    return AppConfig(
        source_db=source_db,
        refresh_seconds=refresh_seconds,
        min_codes=min_codes,
        half_width_px=float(data.get("half_width_px", 300)),
        contour_inset=float(data.get("contour_inset", 0.9)),
        formula_version=int(data.get("formula_version", 1)),
        theme=theme,
        quote_host=quote_host,
        quote_port=quote_port,
        quote_timeout=quote_timeout,
        crash_windows=windows,
        config_path=config_path,
        cache_path=cache_file(),
    )


def save_theme(config: AppConfig, theme: str) -> AppConfig:
    """输入：当前配置，以及 dark 或 light。
    输出：主题已替换的新配置对象。
    用途：顶栏按钮切换主题后写回 config.yaml 的 theme 行。
    边界：主题取值非法，或文件里没有单独的 theme 行时抛出 ValueError，不改文件。
    """
    if theme not in {"dark", "light"}:
        raise ValueError("theme 只能是 dark 或 light")
    text = config.config_path.read_text(encoding="utf-8")
    updated, count = _THEME_LINE.subn(f"theme: {theme}", text, count=1)
    if count != 1:
        raise ValueError("config.yaml 里没有可替换的 theme 行")
    config.config_path.write_text(updated, encoding="utf-8", newline="\n")
    return AppConfig(
        source_db=config.source_db,
        refresh_seconds=config.refresh_seconds,
        min_codes=config.min_codes,
        half_width_px=config.half_width_px,
        contour_inset=config.contour_inset,
        formula_version=config.formula_version,
        theme=theme,
        quote_host=config.quote_host,
        quote_port=config.quote_port,
        quote_timeout=config.quote_timeout,
        crash_windows=config.crash_windows,
        config_path=config.config_path,
        cache_path=config.cache_path,
    )


def refresh_label(seconds: int) -> str:
    """输入：刷新间隔秒数。
    输出：顶栏胶囊文案。
    用途：120 秒显示成「2 min 刷新」，其余显示秒数。
    边界：小于 1 的秒数仍按传入值格式化。
    """
    if seconds > 0 and seconds % 60 == 0:
        return f"{seconds // 60} min 刷新"
    return f"{seconds} 秒刷新"


def _parse_ymd(text: str) -> date:
    """输入：8 位 YYYYMMDD。
    输出：日期。
    用途：解析日期段的一端。
    边界：不可能的月日抛出 ValueError。
    """
    return _make_date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def _make_date(year: int, month: int, day: int) -> date:
    """输入：年、月、日。
    输出：日期。
    用途：把数字收成日期并拒绝非法日历日。
    边界：非法组合抛出 ValueError。
    """
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"非法日期: {year:04d}-{month:02d}-{day:02d}") from exc


def stamp_now() -> str:
    """输入：无。
    输出：本地当前时刻的 ISO 字符串，精确到秒。
    用途：写入缓存的生成时间。
    边界：不带时区。
    """
    return datetime.now().replace(microsecond=0).isoformat()
