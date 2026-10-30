"""历史基底：完整 15 分钟进灰柱，缺一天则整段改为日线。"""

from datetime import datetime
from pathlib import Path

import duckdb

from app.config import AppConfig, load_config
from app.baseline_cache import load_cached
from app.history import recompute_baselines


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
        quote_host="127.0.0.1",
        quote_port=7709,
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
        quote_host=config.quote_host,
        quote_port=config.quote_port,
        quote_timeout=config.quote_timeout,
        crash_windows=("20240604-0605",),
        config_path=config.config_path,
        cache_path=config.cache_path,
    )
    assert load_cached(changed) is None


def test_real_library_keeps_april_on_15m():
    """真实库里 2025-04 来自 15 分钟，且 >6% 不低于同段日线收盘曾经看到的 4903。"""
    config = load_config()
    if not Path(config.source_db).is_file():
        return
    payload = recompute_baselines(config)
    by_id = {item["id"]: item for item in payload["crashes"]}
    april = by_id["20250401-0409"]
    assert april["source"] == "15m"
    assert april["worst"][-1] >= 4903
    assert all(april["worst"][i] <= april["worst"][i + 1] for i in range(10))
    assert by_id["20220307-0315"]["source"] == "1d"
    assert by_id["20220421-0427"]["source"] == "1d"
    assert by_id["20240122-0205"]["label"] == "24-02 日线"
    assert by_id["20240816-20240923"]["source"] == "1d"
    assert "日线" in by_id["20240816-20240923"]["label"]
    bases = [item["base"] for item in payload["bands"]]
    minute = [item["worst"] for item in payload["crashes"] if item["source"] == "15m"]
    assert bases == [max(row[index] for row in minute) for index in range(11)]
    assert all(bases[i] <= bases[i + 1] for i in range(10))
