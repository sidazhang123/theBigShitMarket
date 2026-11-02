"""历史缓存完整结构、诊断状态和单次读取验证，不打开历史行情库。"""

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.baseline_cache import ALGORITHM_VERSION, diagnose_cache, fingerprint, load_cached
from app.config import load_config
from app.count import THRESHOLDS


@pytest.fixture
def cached(tmp_path):
    """输入：临时目录；输出：配置及完整缓存；用途：隔离损坏用例；边界：不读取或覆写实际历史缓存。"""
    config = replace(load_config(), cache_path=tmp_path / "cache.json", crash_windows=("20240604-0604", "20240702-0703"))
    payload = {
        "algorithm_version": ALGORITHM_VERSION,
        "fingerprint": fingerprint(config),
        "formula_version": config.formula_version,
        "min_codes": config.min_codes,
        "source_db": "unused.duckdb",
        "generated_at": "2026-11-02T12:00:00",
        "bands": [{"th": th, "base": index + 1} for index, th in enumerate(THRESHOLDS)],
        "crashes": [
            {"id": "20240604-0604", "label": "24-06", "source": "15m", "worst": list(range(1, 12)), "trading_days": 1, "covered_days": 1},
            {"id": "20240702-0703", "label": "24-07 日线", "source": "1d", "worst": list(range(2, 24, 2)), "trading_days": 2, "covered_days": 1},
        ],
    }
    return config, payload


def _write(config, payload):
    """输入：临时配置和JSON对象；输出：无；用途：准备缓存；边界：只写fixture临时路径。"""
    config.cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_valid_cache_is_ready_and_read_exactly_once(cached, monkeypatch):
    """输入：完整缓存；输出：ready和原数据；用途：避免诊断后重复读文件；边界：仅计数目标缓存读取。"""
    config, payload = cached
    _write(config, payload)
    read = Path.read_text
    calls = []

    def counted(path, *args, **kwargs):
        """输入：读取参数；输出：原文本；用途：记录目标读取次数；边界：其它文件不计数。"""
        if path == config.cache_path:
            calls.append(path)
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    result, status, message = diagnose_cache(config)
    assert result == payload and status == "ready" and message == ""
    assert len(calls) == 1
    assert load_cached(config) == payload
    assert len(calls) == 2


@pytest.mark.parametrize("path,value", [
    (("bands",), []),
    (("bands", 0, "th"), 15),
    (("bands", 0, "base"), -1),
    (("bands", 0, "base"), True),
    (("bands", 0, "base"), 1.0),
    (("bands", 0, "base"), "broken"),
    (("bands", 2, "base"), 0),
    (("bands", 10, "base"), 12),
    (("crashes",), []),
    (("crashes", 0, "id"), "wrong"),
    (("crashes", 0, "source"), "wrong"),
    (("crashes", 0, "label"), "24-13"),
    (("crashes", 0, "label"), "24-07"),
    (("crashes", 0, "label"), "24-06 日线"),
    (("crashes", 0, "worst"), []),
    (("crashes", 0, "worst", 0), True),
    (("crashes", 0, "worst", 0), -1),
    (("crashes", 0, "worst", 2), 0),
    (("crashes", 0, "trading_days"), True),
    (("crashes", 0, "covered_days"), 2),
    (("crashes", 0, "covered_days"), 0),
    (("crashes", 1, "covered_days"), 2),
    (("generated_at",), "not-a-date"),
    (("source_db",), None),
    (("min_codes",), 0),
    (("formula_version",), "1"),
])
def test_matching_fingerprint_does_not_admit_malformed_graph_data(cached, path, value):
    """输入：指纹匹配但字段损坏的缓存；输出：invalid/None；用途：阻止启动崩溃或错图；边界：覆盖布尔/浮点/乱序与关系校验。"""
    config, payload = cached
    payload = copy.deepcopy(payload)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    _write(config, payload)
    assert diagnose_cache(config)[0:2] == (None, "invalid")
    assert load_cached(config) is None


def test_crash_order_and_missing_coverage_fields_are_rejected(cached):
    """输入：日期段倒序或缺失覆盖字段；输出：invalid；用途：保证与配置一一对应；边界：指纹保持匹配。"""
    config, payload = cached
    original = copy.deepcopy(payload)
    payload["crashes"].reverse()
    _write(config, payload)
    assert diagnose_cache(config)[1] == "invalid"
    del original["crashes"][0]["covered_days"]
    _write(config, original)
    assert diagnose_cache(config)[1] == "invalid"


def test_zero_base_retains_valid_daily_contours_and_reports_reason(cached):
    """输入：完整但无可用灰柱基底的缓存；输出：zero_base且保留数据；用途：让界面明确比例尺不可用；边界：日线家数可非零。"""
    config, payload = cached
    for band in payload["bands"]:
        band["base"] = 0
    first = payload["crashes"][0]
    first.update(source="1d", label="24-06 日线", covered_days=0)
    _write(config, payload)
    result, status, message = diagnose_cache(config)
    assert result == payload and status == "zero_base" and message
    assert result["crashes"][1]["worst"][-1] == 22
    assert load_cached(config) == payload


def test_missing_outdated_corrupt_and_unreadable_caches_have_distinct_diagnostics(cached, monkeypatch):
    """输入：缺失、旧算法、错误指纹、损坏或不可读缓存；输出：对应诊断；边界：错误均不向启动传播。"""
    config, payload = cached
    assert diagnose_cache(config)[0:2] == (None, "missing")
    for key, value in (("algorithm_version", ALGORITHM_VERSION - 1), ("fingerprint", "outdated")):
        changed = dict(payload, **{key: value})
        _write(config, changed)
        assert diagnose_cache(config)[0:2] == (None, "mismatch")
    config.cache_path.write_text("{", encoding="utf-8")
    assert diagnose_cache(config)[0:2] == (None, "invalid")

    def denied(path, *args, **kwargs):
        """输入：读取参数；输出：抛权限错误；用途：模拟不可读文件；边界：不改变实际文件权限。"""
        raise PermissionError("read denied")

    monkeypatch.setattr(Path, "read_text", denied)
    assert diagnose_cache(config)[0:2] == (None, "invalid")
