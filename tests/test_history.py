"""历史基底：每天至少一个合格15分钟时间点进灰柱，缺一天则整段日线。"""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from app.config import AppConfig, QuoteHost, load_config
from app.baseline_cache import diagnose_cache, load_cached
from app.history import recompute_baselines

_STANDARD_TIMES = (
    "09:45:00", "10:00:00", "10:15:00", "10:30:00", "10:45:00", "11:00:00", "11:15:00", "11:30:00",
    "13:15:00", "13:30:00", "13:45:00", "14:00:00", "14:15:00", "14:30:00", "14:45:00", "15:00:00",
)
_TIMES_SQL = ", ".join(f"(TIME '{value}')" for value in _STANDARD_TIMES)


def _config(db_path: Path, cache_path: Path, windows: tuple[str, ...], min_codes: int) -> AppConfig:
    """输入：库路径、缓存路径、日期段、覆盖阈值。
    输出：供重算使用的配置。
    用途：测试不读取项目里的 config.yaml。
    边界：主题和比例用固定值，不参与家数。
    """
    return AppConfig(
        source_db=str(db_path),
        refresh_seconds=120,
        min_codes=min_codes,
        half_width_px=300,
        contour_inset=0.9,
        formula_version=1,
        theme="dark",
        quote_hosts=(QuoteHost("127.0.0.1", 7709),),
        quote_timeout=8,
        crash_windows=windows,
        config_path=cache_path.parent / "config.yaml",
        cache_path=cache_path,
    )


def _insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[tuple]) -> None:
    """输入：连接、表名、若干 (代码, 时间, 收盘, 成交量)。
    输出：无。
    用途：往合成库写入很少的 K 线。
    边界：行数很小，不用于行情库。
    """
    con.executemany(
        f"INSERT INTO {table} (code, datetime, close, volume) VALUES (?, ?, ?, ?)",
        rows,
    )


def _build_db(path: Path) -> None:
    """输入：数据库文件路径。
    输出：无。
    用途：造一段每天都有 15 分钟的窗口，和一段缺一天的窗口。
    边界：午盘和指数的暴跌不应进入计数。
    """
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE klines_d (code VARCHAR, datetime TIMESTAMP, close DOUBLE, volume BIGINT)")
    con.execute("CREATE TABLE klines_15 (code VARCHAR, datetime TIMESTAMP, close DOUBLE, volume BIGINT)")
    codes = ["sh.600001", "sh.600002", "sh.600003", "sh.600004"]
    daily = []
    minute = []
    for code in codes:
        daily.append((code, datetime(2024, 6, 3, 15, 0), 10.0, 100))
        daily.append((code, datetime(2024, 7, 1, 15, 0), 10.0, 100))
    prices_d1 = {
        "sh.600001": 8.0,
        "sh.600002": 9.15,
        "sh.600003": 9.35,
        "sh.600004": 9.5,
    }
    for code, close in prices_d1.items():
        daily.append((code, datetime(2024, 6, 4, 15, 0), close, 100))
        minute.append((code, datetime(2024, 6, 4, 10, 0), close, 100))
    for code in codes:
        daily.append((code, datetime(2024, 6, 5, 15, 0), 9.6, 100))
        minute.append((code, datetime(2024, 6, 5, 10, 0), 9.6, 100))
        minute.append((code, datetime(2024, 6, 5, 12, 0), 5.0, 100))
    minute.append(("sh.600001", datetime(2024, 6, 4, 10, 15), 1.0, 0))
    daily.append(("sh.000001", datetime(2024, 6, 3, 15, 0), 10.0, 100))
    daily.append(("sh.000001", datetime(2024, 6, 4, 15, 0), 1.0, 100))
    minute.append(("sh.000001", datetime(2024, 6, 4, 10, 0), 1.0, 100))
    for code in codes:
        daily.append((code, datetime(2024, 7, 2, 15, 0), 8.0, 100))
        daily.append((code, datetime(2024, 7, 3, 15, 0), 8.0, 100))
        minute.append((code, datetime(2024, 7, 2, 10, 0), 8.0, 100))
    _insert(con, "klines_d", daily)
    _insert(con, "klines_15", minute)
    con.execute(f"""
        INSERT INTO klines_15
        SELECT code, CAST(datetime AS DATE) + t.clock, close, volume
        FROM klines_15 CROSS JOIN (VALUES {_TIMES_SQL}) AS t(clock)
        WHERE CAST(datetime AS TIME) = TIME '10:00:00' AND t.clock <> TIME '10:00:00'
    """)
    con.close()


def test_complete_window_sets_base_and_gap_stays_daily(tmp_path: Path):
    """两天都有合格 15 分钟的段决定基底；缺一天的段只出日线轮廓，且家数不乘 0.9。"""
    db_path = tmp_path / "mini.duckdb"
    _build_db(db_path)
    config = _config(
        db_path,
        tmp_path / "baselines.json",
        ("20240604-0605", "20240702-0703"),
        min_codes=3,
    )
    payload = recompute_baselines(config)
    by_id = {item["id"]: item for item in payload["crashes"]}
    full = by_id["20240604-0605"]
    sparse = by_id["20240702-0703"]
    assert full["source"] == "15m"
    assert full["label"] == "24-06"
    assert full["worst"] == [1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 3]
    assert sparse["source"] == "1d"
    assert sparse["label"] == "24-07 日线"
    assert sparse["covered_days"] == 1
    assert sparse["trading_days"] == 2
    assert sparse["worst"][0] == 4
    bases = [item["base"] for item in payload["bands"]]
    assert bases[0] == 1
    assert bases[-1] == 3
    assert all(bases[i] <= bases[i + 1] for i in range(10))
    assert load_cached(config)["fingerprint"] == payload["fingerprint"]
    changed = AppConfig(
        source_db=config.source_db,
        refresh_seconds=config.refresh_seconds,
        min_codes=config.min_codes,
        half_width_px=config.half_width_px,
        contour_inset=config.contour_inset,
        formula_version=config.formula_version,
        theme=config.theme,
        quote_hosts=config.quote_hosts,
        quote_timeout=config.quote_timeout,
        crash_windows=("20240604-0605",),
        config_path=config.config_path,
        cache_path=config.cache_path,
    )
    assert load_cached(changed) is None


def test_real_library_uses_daily_minute_coverage(tmp_path: Path):
    """输入：当前真实库和日期段；输出：独立缓存；边界：源库不存在则跳过，绝不覆写项目缓存。"""
    config = load_config()
    if not Path(config.source_db).is_file():
        pytest.skip("本机没有历史行情库")
    config = replace(config, cache_path=tmp_path / "real-baselines.json")
    payload = recompute_baselines(config)
    by_id = {item["id"]: item for item in payload["crashes"]}
    assert list(by_id) == list(config.crash_windows)
    if "20250401-0409" in by_id:
        april = by_id["20250401-0409"]
        assert april["worst"][-1] >= 4903
    for crash in by_id.values():
        assert all(crash["worst"][i] <= crash["worst"][i + 1] for i in range(10))
        assert (crash["source"] == "15m") == (crash["trading_days"] > 0 and crash["covered_days"] == crash["trading_days"])
        assert ("日线" in crash["label"]) == (crash["source"] == "1d")
    bases = [item["base"] for item in payload["bands"]]
    minute = [item["worst"] for item in payload["crashes"] if item["source"] == "15m"]
    assert bases == [max((row[index] for row in minute), default=0) for index in range(11)]
    assert all(bases[i] <= bases[i + 1] for i in range(10))
    assert diagnose_cache(config)[1] in {"ready", "zero_base"}


def test_history_exact_price_thresholds_are_excluded(tmp_path: Path):
    """输入：精确跌幅门槛及略超门槛的合成行情；输出：日线和分钟一致家数；边界：浮点尾差不越档。"""
    db_path = tmp_path / "thresholds.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(f"""
        CREATE TABLE klines_d AS
        WITH stocks AS (
          SELECT 'sh.600' || lpad(CAST(i AS VARCHAR), 3, '0') AS code,
                 100 - (6 + i // 2) - (i % 2) * 0.000001 AS close
          FROM range(22) t(i)
        )
        SELECT code, TIMESTAMP '2024-06-03 15:00:00' AS datetime, 100.0 AS close, 100 AS volume FROM stocks
        UNION ALL
        SELECT code, TIMESTAMP '2024-06-04 15:00:00', close, 100 FROM stocks;
        CREATE TABLE klines_15 AS
        SELECT code, CAST(datetime AS DATE) + t.clock AS datetime, close, volume
        FROM klines_d CROSS JOIN (VALUES {_TIMES_SQL}) AS t(clock)
        WHERE CAST(datetime AS DATE) = DATE '2024-06-04';
    """)
    con.close()
    config = _config(db_path, tmp_path / "thresholds.json", ("20240604-0604",), 1)
    payload = recompute_baselines(config)
    assert payload["crashes"][0]["worst"] == list(range(1, 22, 2))
    config = replace(config, min_codes=23)
    payload = recompute_baselines(config)
    assert payload["crashes"][0]["source"] == "1d"
    assert payload["crashes"][0]["worst"] == list(range(1, 22, 2))


@pytest.mark.parametrize("contents", ["null", "[]", "{", '{"algorithm_version": 2, "formula_version": "bad"}'])
def test_invalid_cache_is_ignored(tmp_path: Path, contents: str):
    """输入：损坏或格式错误的缓存；输出：None；用途：保证启动稳健；边界：不连接源库。"""
    cache_path = tmp_path / "invalid.json"
    config = _config(tmp_path / "missing.duckdb", cache_path, ("20240604-0604",), 1)
    cache_path.write_text(contents, encoding="utf-8")
    assert load_cached(config) is None


@pytest.mark.parametrize("mutation", [
    "DELETE FROM klines_15 WHERE datetime = TIMESTAMP '2024-06-04 10:00:00'",
    "UPDATE klines_15 SET datetime = TIMESTAMP '2024-06-04 09:46:00' WHERE datetime = TIMESTAMP '2024-06-04 10:00:00'",
    """DELETE FROM klines_15 WHERE code = 'sh.600004' AND datetime = TIMESTAMP '2024-06-04 10:00:00';
       INSERT INTO klines_15 SELECT * FROM klines_15 WHERE code = 'sh.600001' AND datetime = TIMESTAMP '2024-06-04 10:00:00'""",
])
def test_remaining_qualified_timestamp_each_day_keeps_minute_window(tmp_path: Path, mutation: str):
    """输入：少一根、时段内偏移分钟或局部股票不足；输出：仍用15分钟；边界：每天至少一个时间点达标即可。"""
    path = tmp_path / "bars.duckdb"
    _build_db(path)
    con = duckdb.connect(str(path))
    try:
        con.execute(mutation)
    finally:
        con.close()
    config = _config(path, tmp_path / "bars.json", ("20240604-0605",), 4)
    crash = recompute_baselines(config)["crashes"][0]
    assert crash["source"] == "15m"
    assert crash["covered_days"] == crash["trading_days"] == 2


def test_duplicate_bars_and_lunch_break_spike_do_not_inflate_counts(tmp_path: Path):
    """输入：重复股票时间行及午休异常低价；输出：唯一股票计数不变；边界：午休不参与计数。"""
    path = tmp_path / "duplicates.duckdb"
    _build_db(path)
    config = _config(path, tmp_path / "duplicates.json", ("20240604-0605",), 4)
    original = recompute_baselines(config)
    con = duckdb.connect(str(path))
    try:
        con.execute("""
            INSERT INTO klines_15 SELECT * FROM klines_15;
            INSERT INTO klines_15 SELECT code, TIMESTAMP '2024-06-04 12:00:00', 0.01, 100
            FROM klines_d WHERE datetime = TIMESTAMP '2024-06-04 15:00:00';
        """)
    finally:
        con.close()
    actual = recompute_baselines(config)
    assert actual["crashes"] == original["crashes"]
    assert actual["bands"] == original["bands"]


@pytest.mark.parametrize("clock", ["10:00:00", "10:01:00"])
def test_one_qualified_timestamp_per_day_is_sufficient(tmp_path: Path, clock: str):
    """输入：每天仅一根且可为时段内非标准分钟；输出：15m覆盖；用途：保持按天的原口径；边界：不要求16根。"""
    path = tmp_path / "one-bar.duckdb"
    _build_db(path)
    con = duckdb.connect(str(path))
    try:
        con.execute(f"""
            DELETE FROM klines_15 WHERE CAST(datetime AS TIME) <> TIME '10:00:00';
            UPDATE klines_15 SET datetime = CAST(datetime AS DATE) + TIME '{clock}';
        """)
    finally:
        con.close()
    config = _config(path, tmp_path / "one-bar.json", ("20240604-0605",), 4)
    crash = recompute_baselines(config)["crashes"][0]
    assert crash["source"] == "15m"
    assert crash["covered_days"] == crash["trading_days"] == 2
    assert "qualified_bars" not in crash and "expected_bars" not in crash


def test_duplicate_stocks_cannot_turn_an_unqualified_day_into_covered(tmp_path: Path):
    """输入：每日一根、首日仅3只唯一股票却有重复行；输出：首日不覆盖并整段日线；边界：min_codes按唯一股票。"""
    path = tmp_path / "duplicate-coverage.duckdb"
    _build_db(path)
    con = duckdb.connect(str(path))
    try:
        con.execute("""
            DELETE FROM klines_15 WHERE CAST(datetime AS TIME) <> TIME '10:00:00';
            DELETE FROM klines_15 WHERE code = 'sh.600004' AND CAST(datetime AS DATE) = DATE '2024-06-04';
            INSERT INTO klines_15 SELECT * FROM klines_15 WHERE code = 'sh.600001' AND CAST(datetime AS DATE) = DATE '2024-06-04';
        """)
    finally:
        con.close()
    config = _config(path, tmp_path / "duplicate-coverage.json", ("20240604-0605",), 4)
    crash = recompute_baselines(config)["crashes"][0]
    assert crash["source"] == "1d"
    assert crash["covered_days"] == 1 and crash["trading_days"] == 2


@pytest.mark.parametrize("windows", [(), ("20240604-0605", "20240604-20240605")])
def test_recompute_rejects_empty_or_equivalent_duplicate_windows_before_opening_db(tmp_path: Path, windows):
    """输入：空段或两种写法的相同日期范围；输出：ValueError；边界：在打开数据库和写缓存之前拒绝。"""
    config = _config(tmp_path / "does-not-exist.duckdb", tmp_path / "cache.json", windows, 1)
    with pytest.raises(ValueError, match="重复|非空"):
        recompute_baselines(config)
    assert not config.cache_path.exists()
