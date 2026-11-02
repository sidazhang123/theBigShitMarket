"""读取已经算好的历史基底。

只解析本地 JSON。不导入数据库，也不连接行情库。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from app.config import AppConfig, parse_window
from app.count import THRESHOLDS

ALGORITHM_VERSION = 4
_LABEL = re.compile(r"^\d{2}-(?:0[1-9]|1[0-2])(?: 日线)?$")


def fingerprint(config: AppConfig) -> str:
    """输入：配置。
    输出：十六进制指纹。
    用途：判断缓存是否仍对应当前算法、日期段、覆盖阈值和公式版本。
    边界：不包含行情库路径。半宽和内缩也不参与。
    """
    payload = {
        "algorithm_version": ALGORITHM_VERSION,
        "min_codes": config.min_codes,
        "formula_version": config.formula_version,
        "windows": list(config.crash_windows),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cached(config: AppConfig) -> dict[str, Any] | None:
    """输入：配置。
    输出：可用的历史缓存；没有文件或内容对不上时返回 None。
    用途：启动时读灰色柱和虚线，不打开行情库。
    边界：缓存损坏时返回 None，不抛出。
    """
    payload, _, _ = diagnose_cache(config)
    return payload


def diagnose_cache(config: AppConfig) -> tuple[dict[str, Any] | None, str, str]:
    """输入：运行配置；输出：缓存、ready/missing/mismatch/invalid/zero_base 状态及中文说明。
    用途：只读一次文件，校验全部图形数据并向界面解释无法加载的原因。
    边界：失效缓存返回 None；全零但合法的缓存保留日线轮廓，不连接历史数据库。
    """
    try:
        data = json.loads(config.cache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing", "历史基底缓存缺失，请重新计算历史缓存"
    except (OSError, ValueError, TypeError):
        return None, "invalid", "历史缓存无法读取或格式损坏，请重新计算历史缓存"
    if not isinstance(data, dict):
        return None, "invalid", "历史缓存结构无效，请重新计算历史缓存"
    if type(data.get("algorithm_version")) is not int or not isinstance(data.get("fingerprint"), str):
        return None, "invalid", "历史缓存版本或指纹缺失，请重新计算历史缓存"
    if data["algorithm_version"] != ALGORITHM_VERSION or data["fingerprint"] != fingerprint(config):
        return None, "mismatch", "历史缓存与当前算法或日期段不匹配，请重新计算历史缓存"
    try:
        valid = _valid_payload(data, config)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        valid = False
    if not valid:
        return None, "invalid", "历史缓存档位、轮廓或覆盖信息无效，请重新计算历史缓存"
    if data["bands"][-1]["base"] == 0:
        return data, "zero_base", "历史基底为零，无法按历史比例绘图；请检查完整分钟数据和日期段"
    return data, "ready", ""


def _valid_counts(values: object) -> bool:
    """输入：各档家数；输出：是否为11个非负整数且按深到浅单调；边界：布尔值不视为整数。"""
    return (
        isinstance(values, list)
        and len(values) == len(THRESHOLDS)
        and all(type(value) is int and value >= 0 for value in values)
        and all(left <= right for left, right in zip(values, values[1:]))
    )


def _valid_payload(data: dict[str, Any], config: AppConfig) -> bool:
    """输入：指纹已匹配的缓存和配置；输出：全部字段及相互关系是否有效。
    用途：阻止合法 JSON 中损坏的图形数据进入启动或页面。
    边界：不接受缺档、重复或乱序日期段、错误来源/标签以及与分钟轮廓不一致的灰柱。
    """
    if type(data.get("formula_version")) is not int or data["formula_version"] != config.formula_version:
        return False
    if type(data.get("min_codes")) is not int or data["min_codes"] != config.min_codes:
        return False
    if not isinstance(data.get("source_db"), str) or not isinstance(data.get("generated_at"), str):
        return False
    datetime.fromisoformat(data["generated_at"])
    bands = data.get("bands")
    crashes = data.get("crashes")
    if not isinstance(bands, list) or len(bands) != len(THRESHOLDS):
        return False
    if any(not isinstance(band, dict) or type(band.get("th")) is not int or band["th"] != th for band, th in zip(bands, THRESHOLDS)):
        return False
    bases = [band.get("base") for band in bands]
    if not _valid_counts(bases) or not isinstance(crashes, list) or len(crashes) != len(config.crash_windows):
        return False
    expected_bases = [0] * len(THRESHOLDS)
    for crash, wid in zip(crashes, config.crash_windows):
        if not isinstance(crash, dict) or crash.get("id") != wid or not _valid_counts(crash.get("worst")):
            return False
        source, label = crash.get("source"), crash.get("label")
        if source not in {"15m", "1d"} or not isinstance(label, str) or _LABEL.fullmatch(label) is None:
            return False
        if label.endswith(" 日线") != (source == "1d"):
            return False
        start, end = parse_window(wid)
        year_suffix, month = int(label[:2]), int(label[3:5])
        if not any(
            (start.year, start.month) <= (century * 100 + year_suffix, month) <= (end.year, end.month)
            for century in range(start.year // 100, end.year // 100 + 1)
        ):
            return False
        days = [crash.get(key) for key in ("trading_days", "covered_days")]
        if any(type(value) is not int or value < 0 for value in days):
            return False
        trading, covered = days
        if covered > trading or trading > (end - start).days + 1:
            return False
        complete = trading > 0 and covered == trading
        if (source == "15m") != complete:
            return False
        if source == "15m":
            expected_bases = [max(base, worst) for base, worst in zip(expected_bases, crash["worst"])]
    return bases == expected_bases
