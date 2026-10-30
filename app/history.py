"""历史基底和股灾轮廓。

只读打开行情库，在 DuckDB 内按时间戳聚合，Python 只接收每段一行。
一段里每个交易日都有合格 15 分钟，才把该段计入灰色基底；否则整段用日线收盘，并在日期牌标明日线。
缓存保存原始家数。0.9 内缩留到绘制时再乘。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any, Sequence

import duckdb

from app.config import AppConfig, parse_window, stamp_now
from app.count import THRESHOLDS

# 股票代码前缀与全市场股票集合对齐。库内代码带市场前缀。
_UNIVERSE = """
(
  code LIKE 'sh.60%' OR code LIKE 'sh.68%'
  OR code LIKE 'sz.000%' OR code LIKE 'sz.001%' OR code LIKE 'sz.002%' OR code LIKE 'sz.003%'
  OR code LIKE 'sz.300%' OR code LIKE 'sz.301%'
  OR code LIKE 'bj.43%' OR code LIKE 'bj.83%' OR code LIKE 'bj.87%' OR code LIKE 'bj.88%' OR code LIKE 'bj.92%'
)
"""

_SESSION = """
(
  CAST(datetime AS TIME) BETWEEN TIME '09:45:00' AND TIME '11:30:00'
  OR CAST(datetime AS TIME) BETWEEN TIME '13:15:00' AND TIME '15:00:00'
)
"""

_PAD_DAYS = 30


def fingerprint(config: AppConfig) -> str:
    """输入：配置。
    输出：十六进制指纹。
    用途：判断缓存是否仍对应当前日期段、库路径、覆盖阈值和公式版本。
    边界：半宽、内缩和行情库路径不参与指纹。路径只在主动重算时使用。
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
    输出：指纹一致的缓存；没有文件或指纹不同时返回 None。
    用途：启动时跳过行情库扫描。
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
    用途：行情库路径留空时仍使用已经算好的缓存。
    边界：缺少各段结果时返回假。
    """
    if int(data.get("formula_version", -1)) != int(config.formula_version):
        return False
    if int(data.get("min_codes", -1)) != int(config.min_codes):
        return False
    ids = [item.get("id") for item in data.get("crashes") or []]
    return bool(data.get("bands")) and ids == list(config.crash_windows)


def recompute_baselines(config: AppConfig) -> dict[str, Any]:
    """输入：配置，含行情库路径、日期段和 min_codes。
    输出：基底和各段轮廓，并写入缓存。
    用途：重算灰色基底和每次股灾虚线的唯一入口。
    边界：只读打开数据库。没有任何完整 15 分钟段时，各档基底为 0。日期段非法时抛出 ValueError。
    """
    windows = [_Window(item, *parse_window(item)) for item in config.crash_windows]
    if not str(config.source_db).strip():
        raise ValueError("重算历史基底需要 source_db")
    con = duckdb.connect(config.source_db, read_only=True)
    try:
        con.execute("SET enable_progress_bar=false")
        rows = con.execute(_sql(windows, config.min_codes)).fetchall()
    finally:
        con.close()
    by_id = {row[0]: row for row in rows}
    crashes: list[dict[str, Any]] = []
    for window in windows:
        crashes.append(_crash_from_row(window, by_id.get(window.wid)))
    bases = [0] * len(THRESHOLDS)
    for crash in crashes:
        if crash["source"] != "15m":
            continue
        for index, value in enumerate(crash["worst"]):
            if value > bases[index]:
                bases[index] = value
    payload = {
        "fingerprint": fingerprint(config),
        "generated_at": stamp_now(),
        "formula_version": config.formula_version,
        "source_db": config.source_db,
        "min_codes": config.min_codes,
        "bands": [{"th": th, "base": base} for th, base in zip(THRESHOLDS, bases)],
        "crashes": crashes,
    }
    config.cache_path.parent.mkdir(parents=True, exist_ok=True)
    config.cache_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


class _Window:
    """一段股灾日期。wid 使用配置原文，避免两条相同日期被收成一段。"""

    def __init__(self, wid: str, start: date, end: date) -> None:
        self.wid = wid
        self.start = start
        self.end = end


def _crash_from_row(window: _Window, row: tuple[Any, ...] | None) -> dict[str, Any]:
    """输入：日期段，以及 SQL 的一行；没有行时视为整段无数据。
    输出：一段轮廓的字典，worst 是原始家数。
    用途：决定这段用 15 分钟还是日线，并生成 yy-mm 标签。
    边界：交易日为 0，或合格 15 分钟没有覆盖每一个交易日时，来源是日线。
    """
    if row is None:
        trading_days = 0
        covered_days = 0
        daily = [0] * len(THRESHOLDS)
        minute = [0] * len(THRESHOLDS)
        daily_peak = None
        minute_peak = None
    else:
        # wid, trading_days, covered_days, d16..d06, m16..m06, daily_peak, minute_peak
        trading_days = int(row[1] or 0)
        covered_days = int(row[2] or 0)
        offset = 3
        width = len(THRESHOLDS)
        daily = [int(row[offset + index] or 0) for index in range(width)]
        minute = [int(row[offset + width + index] or 0) for index in range(width)]
        daily_peak = row[offset + width * 2]
        minute_peak = row[offset + width * 2 + 1]
    complete = trading_days > 0 and covered_days == trading_days
    source = "15m" if complete else "1d"
    worst = minute if complete else daily
    peak = minute_peak if complete else daily_peak
    return {
        "id": window.wid,
        "label": _label(peak, window.start, daily=not complete),
        "source": source,
        "worst": worst,
        "trading_days": trading_days,
        "covered_days": covered_days,
    }


def _label(peak: str | None, fallback: date, daily: bool) -> str:
    """输入：峰值日期文本 YYYY-MM-DD、段开始日、是否日线。
    输出：yy-mm，日线另加「 日线」。
    用途：日期牌只标月份。
    边界：没有峰值日期时用段开始月。
    """
    if peak:
        year = int(str(peak)[:4])
        month = int(str(peak)[5:7])
    else:
        year = fallback.year
        month = fallback.month
    text = f"{year % 100:02d}-{month:02d}"
    if daily:
        text += " 日线"
    return text


def _sql(windows: Sequence[_Window], min_codes: int) -> str:
    """输入：日期段和单根 15 分钟的最少有效股票数。
    输出：一条只返回每段一行的 DuckDB SQL。
    用途：把日线各档最大、15 分钟各档最大和覆盖天数留在库内。
    边界：min_codes 必须是整数。日期来自已解析的 date，不拼接用户原始字符串进日期字面量。
    """
    pad_rows = []
    daily_ranges = []
    minute_ranges = []
    for window in windows:
        pad = window.start - timedelta(days=_PAD_DAYS)
        end_next = window.end + timedelta(days=1)
        wid = window.wid.replace("'", "''")
        pad_rows.append(
            f"('{wid}', DATE '{pad.isoformat()}', DATE '{window.start.isoformat()}', DATE '{window.end.isoformat()}')"
        )
        daily_ranges.append(
            f"(datetime >= TIMESTAMP '{pad.isoformat()} 00:00:00' "
            f"AND datetime < TIMESTAMP '{end_next.isoformat()} 00:00:00')"
        )
        minute_ranges.append(
            f"(datetime >= TIMESTAMP '{window.start.isoformat()} 00:00:00' "
            f"AND datetime < TIMESTAMP '{end_next.isoformat()} 00:00:00')"
        )
    tier_filter = _filters("r", "c")
    daily_max = ", ".join(
        f"coalesce(max(c{th:02d}), 0) AS d{th:02d}" for th in THRESHOLDS
    )
    minute_max = ", ".join(
        f"coalesce(max(c{th:02d}), 0) AS m{th:02d}" for th in THRESHOLDS
    )
    return f"""
WITH pads(wid, p0, d0, d1) AS (
  VALUES {", ".join(pad_rows)}
),
cal_days AS (
  SELECT DISTINCT CAST(datetime AS DATE) AS d
  FROM klines_d
  WHERE {" OR ".join(daily_ranges)}
),
seq AS (
  SELECT p.wid, c.d,
         lag(c.d) OVER (PARTITION BY p.wid ORDER BY c.d) AS prev_d
  FROM pads p
  JOIN cal_days c ON c.d BETWEEN p.p0 AND p.d1
),
need AS (
  SELECT s.wid, s.d, s.prev_d
  FROM seq s
  JOIN pads p ON p.wid = s.wid
  WHERE s.d BETWEEN p.d0 AND p.d1
),
univ AS (
  SELECT code, CAST(datetime AS DATE) AS d, max(close) AS close
  FROM klines_d
  WHERE close > 0 AND volume > 0 AND isfinite(close)
    AND {_UNIVERSE}
    AND ({" OR ".join(daily_ranges)})
  GROUP BY 1, 2
),
daily_ret AS (
  SELECT n.wid, u.d, u.close / p.close - 1 AS r
  FROM need n
  JOIN univ u ON u.d = n.d
  JOIN univ p ON p.code = u.code AND p.d = n.prev_d
  WHERE p.close > 0 AND isfinite(p.close) AND isfinite(u.close)
),
daily_hit AS (
  SELECT wid, d,
    {tier_filter}
  FROM daily_ret
  GROUP BY 1, 2
),
bars AS (
  SELECT code, datetime, CAST(datetime AS DATE) AS d, close
  FROM klines_15
  WHERE close > 0 AND volume > 0 AND isfinite(close)
    AND {_UNIVERSE}
    AND {_SESSION}
    AND ({" OR ".join(minute_ranges)})
),
minute_ret AS (
  SELECT n.wid, b.datetime, b.close / p.close - 1 AS r
  FROM bars b
  JOIN need n ON b.d = n.d
  JOIN univ p ON p.code = b.code AND p.d = n.prev_d
  WHERE p.close > 0 AND isfinite(p.close)
),
minute_hit AS (
  SELECT wid, datetime,
    {tier_filter},
    count(*) AS n
  FROM minute_ret
  GROUP BY 1, 2
),
qualified AS (
  SELECT * FROM minute_hit WHERE n >= {int(min_codes)}
),
day_max AS (
  SELECT wid, {daily_max}
  FROM daily_hit
  GROUP BY wid
),
minute_max AS (
  SELECT wid, {minute_max}
  FROM qualified
  GROUP BY wid
),
covered AS (
  SELECT wid, count(DISTINCT CAST(datetime AS DATE)) AS covered_days
  FROM qualified
  GROUP BY wid
),
trade_n AS (
  SELECT wid, count(*) AS trading_days
  FROM need
  GROUP BY wid
),
dpeak AS (
  SELECT wid, d AS daily_peak
  FROM (
    SELECT wid, d, c06,
           row_number() OVER (PARTITION BY wid ORDER BY c06 DESC, d ASC) AS rn
    FROM daily_hit
  ) ranked
  WHERE rn = 1
),
mpeak AS (
  SELECT wid, CAST(datetime AS DATE) AS minute_peak
  FROM (
    SELECT wid, datetime, c06,
           row_number() OVER (PARTITION BY wid ORDER BY c06 DESC, datetime ASC) AS rn
    FROM qualified
  ) ranked
  WHERE rn = 1
)
SELECT
  p.wid,
  coalesce(t.trading_days, 0) AS trading_days,
  coalesce(c.covered_days, 0) AS covered_days,
  {", ".join(f"coalesce(d.d{th:02d}, 0)" for th in THRESHOLDS)},
  {", ".join(f"coalesce(m.m{th:02d}, 0)" for th in THRESHOLDS)},
  strftime(dp.daily_peak, '%Y-%m-%d') AS daily_peak,
  strftime(mp.minute_peak, '%Y-%m-%d') AS minute_peak
FROM pads p
LEFT JOIN trade_n t ON t.wid = p.wid
LEFT JOIN covered c ON c.wid = p.wid
LEFT JOIN day_max d ON d.wid = p.wid
LEFT JOIN minute_max m ON m.wid = p.wid
LEFT JOIN dpeak dp ON dp.wid = p.wid
LEFT JOIN mpeak mp ON mp.wid = p.wid
"""


def _filters(expr: str, prefix: str) -> str:
    """输入：收益率表达式和列前缀。
    输出：11 个累计计数表达式。
    用途：同一行上按门槛生成家数，深档在前。
    边界：比较是严格小于。
    """
    return ",\n".join(
        f"count(*) FILTER (WHERE {expr} < -{th}/100.0) AS {prefix}{th:02d}"
        for th in THRESHOLDS
    )
