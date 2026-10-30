"""读取已经算好的历史基底。

只解析本地 JSON。不导入数据库，也不连接行情库。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config import AppConfig


def fingerprint(config: AppConfig) -> str:
    """输入：配置。
    输出：十六进制指纹。
    用途：判断缓存是否仍对应当前日期段、覆盖阈值和公式版本。
    边界：不包含行情库路径。半宽和内缩也不参与。
    """
    payload = {
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
    path = config.cache_path
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("fingerprint") == fingerprint(config):
        return data
    if _cache_matches(data, config):
        return data
    return None


def _cache_matches(data: dict[str, Any], config: AppConfig) -> bool:
    """输入：缓存内容和当前配置。
    输出：家数是否仍对应当前日期段、覆盖阈值和公式版本。
    用途：旧缓存指纹里带有行情库路径时，启动仍然采用这份结果。
    边界：缺少各段结果时返回假。
    """
    if int(data.get("formula_version", -1)) != int(config.formula_version):
        return False
    if int(data.get("min_codes", -1)) != int(config.min_codes):
        return False
    ids = [item.get("id") for item in data.get("crashes") or []]
    return bool(data.get("bands")) and ids == list(config.crash_windows)
