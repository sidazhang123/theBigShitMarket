"""读取和回写 config.yaml。

用户可调项只放在配置文件里。本模块不解析命令行。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

import yaml

from app.paths import cache_file, runtime_root
from app.storage import atomic_update_text

DEFAULT_CONFIG_PATH = runtime_root() / "config.yaml"

REFRESH_CHOICES = (60, 120, 180, 240, 300)


@dataclass(frozen=True)
class QuoteHost:
    """标准行情端点。列表顺序就是每轮故障切换的优先顺序。"""

    host: str
    port: int


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
    quote_hosts: tuple[QuoteHost, ...]
    quote_timeout: float
    crash_windows: tuple[str, ...]
    config_path: Path
    cache_path: Path
    quote_deadline_seconds: float = 30.0
    quote_min_stocks: int = 4500
    quote_min_coverage: float = 0.9


class _UniqueKeyLoader(yaml.SafeLoader):
    """拒绝重复键的安全 YAML 加载器，避免保存与读取取到不同设置。"""

    def construct_mapping(self, node, deep=False):
        """输入：映射节点；输出：字典；用途：验证键唯一；边界：复杂或重复键明确报错。"""
        keys = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in keys:
                raise ValueError(f"配置键必须是唯一字符串：{key!r}")
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


def _positive_int(data: dict, key: str, default: int) -> int:
    """输入：配置映射、键和缺省值；输出：正整数；边界：布尔值和非整数拒绝。"""
    value = data.get(key, default)
    if type(value) is not int or value < 1:
        raise ValueError(f"{key} 必须是正整数")
    return value


def _positive_float(data: dict, key: str, default: float, maximum: float | None = None) -> float:
    """输入：配置、键、缺省值和可选上限；输出：有限正数；边界：布尔值、非数值和越界拒绝。"""
    raw = data.get(key, default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{key} 必须是有限正数")
    value = float(raw)
    if not math.isfinite(value) or value <= 0 or (maximum is not None and value > maximum):
        raise ValueError(f"{key} 必须是有限正数" + (f"且不大于 {maximum}" if maximum else ""))
    return value


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
    data = yaml.load(config_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    if not isinstance(data, dict):
        raise ValueError("config.yaml 顶层必须是配置映射")
    raw_windows = data.get("crash_windows")
    if not isinstance(raw_windows, list) or not raw_windows or not all(isinstance(item, str) for item in raw_windows):
        raise ValueError("config.yaml 的 crash_windows 必须是非空日期段列表")
    windows = tuple(item.strip() for item in raw_windows)
    parsed = set()
    for item in windows:
        period = parse_window(item)
        if period in parsed:
            raise ValueError(f"crash_windows 包含重复日期段：{item}")
        parsed.add(period)
    theme = str(data.get("theme") or "dark").strip().lower()
    if theme not in {"dark", "light"}:
        raise ValueError("theme 只能是 dark 或 light")
    refresh_seconds = _positive_int(data, "refresh_seconds", 120)
    min_codes = _positive_int(data, "min_codes", 4500)
    source_db = str(data.get("source_db") or "").strip()
    quote_hosts = _parse_quote_hosts(data.get("quote_hosts"))
    quote_timeout = _positive_float(data, "quote_timeout", 8)
    return AppConfig(
        source_db=source_db,
        refresh_seconds=refresh_seconds,
        min_codes=min_codes,
        half_width_px=_positive_float(data, "half_width_px", 300),
        contour_inset=_positive_float(data, "contour_inset", 0.9, 1),
        formula_version=_positive_int(data, "formula_version", 1),
        theme=theme,
        quote_hosts=quote_hosts,
        quote_timeout=quote_timeout,
        crash_windows=windows,
        config_path=config_path,
        cache_path=cache_file(),
        quote_deadline_seconds=_positive_float(data, "quote_deadline_seconds", 30),
        quote_min_stocks=_positive_int(data, "quote_min_stocks", 4500),
        quote_min_coverage=_positive_float(data, "quote_min_coverage", 0.9, 1),
    )


def _parse_quote_hosts(raw: object) -> tuple[QuoteHost, ...]:
    """输入：quote_hosts 配置值；输出：有序且去重的标准行情端点。
    用途：校验故障切换列表，避免运行时才发现空地址或非法端口。
    边界：必须为非空列表，每项含非空 host；port 缺省 7709，必须是 1～65535 的整数。
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("quote_hosts 必须是非空列表，每项包含 host 和 port")
    hosts: list[QuoteHost] = []
    seen: set[QuoteHost] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"quote_hosts 第 {index} 项必须包含 host 和 port")
        host = item.get("host")
        port = item.get("port", 7709)
        if not isinstance(host, str) or not host.strip() or any(char.isspace() for char in host.strip()):
            raise ValueError(f"quote_hosts 第 {index} 项 host 必须是非空地址")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError(f"quote_hosts 第 {index} 项 port 必须是 1～65535 的整数")
        endpoint = QuoteHost(host.strip(), port)
        if endpoint not in seen:
            seen.add(endpoint)
            hosts.append(endpoint)
    return tuple(hosts)


def save_theme(config: AppConfig, theme: str) -> AppConfig:
    """输入：当前配置，以及 dark 或 light。
    输出：主题已替换的新配置对象。
    用途：顶栏按钮切换主题后写回 config.yaml 的 theme 行。
    边界：非法主题抛出 ValueError；配置缺少 theme 时补齐，不改其它配置。
    """
    if theme not in {"dark", "light"}:
        raise ValueError("theme 只能是 dark 或 light")
    _save_setting(config.config_path, "theme", theme)
    return replace(config, theme=theme)


def save_refresh_seconds(config: AppConfig, seconds: int) -> AppConfig:
    """输入：当前配置和 1～5 分钟对应的整数秒数。
    输出：刷新间隔已保存的新配置。
    用途：下拉选择后记住实际轮询周期。
    边界：布尔值、非整数和不在选项内的值拒绝写入；缺省键会补齐。
    """
    if type(seconds) is not int or seconds not in REFRESH_CHOICES:
        raise ValueError("刷新间隔只能是 1、2、3、4、5 分钟")
    _save_setting(config.config_path, "refresh_seconds", str(seconds))
    return replace(config, refresh_seconds=seconds)


def _save_setting(path: Path, key: str, value: str) -> None:
    """输入：配置路径、受控的顶层键和标量文本。
    输出：无，写回 UTF-8 配置。
    用途：保留其它设置与行尾注释，只更新所选项。
    边界：键缺省时追加；调用方须串行化并校验值。
    """
    pattern = re.compile(rf"(?m)^{re.escape(key)}:[^\S\r\n]*[^#\r\n]*(?P<comment>#[^\r\n]*)?$")
    def replacement(match: re.Match) -> str:
        """输入：配置项匹配；输出：保留注释的新行；边界：无注释时不追加空格。"""
        comment = match.group("comment")
        return f"{key}: {value}" + (f"  {comment}" if comment else "")
    def transform(text: str) -> str:
        """输入：锁内最新配置；输出：目标键的新文本；边界：先拒绝重复键，缺省项补齐。"""
        data = yaml.load(text, Loader=_UniqueKeyLoader)
        if not isinstance(data, dict):
            raise ValueError("配置文件必须为映射")
        updated, count = pattern.subn(replacement, text, count=1)
        if count == 0:
            node = yaml.compose(text)
            if key in data:
                for key_node, value_node in node.value:
                    if key_node.value == key and value_node.start_mark.index >= key_node.end_mark.index:
                        return text[:value_node.start_mark.index] + value + text[value_node.end_mark.index:]
            if node.flow_style or key in data:
                data[key] = yaml.safe_load(value)
                return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
            updated = text.rstrip("\n") + f"\n{key}: {value}\n"
        return updated
    atomic_update_text(path, transform)


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
