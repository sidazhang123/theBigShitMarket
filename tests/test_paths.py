"""打包后把新缓存写到 exe 旁边。"""

from pathlib import Path

from app.paths import refresh_shipped_data


def test_refresh_replaces_cache_and_keeps_theme(tmp_path: Path):
    """旧缓存被发布内容替换。日期段跟上，主题保留。"""
    bundled = tmp_path / "bundled"
    root = tmp_path / "root"
    (bundled / "cache").mkdir(parents=True)
    (root / "cache").mkdir(parents=True)
    (bundled / "cache" / "baselines.json").write_text('{"n":2}\n', encoding="utf-8")
    (root / "cache" / "baselines.json").write_text('{"n":1}\n', encoding="utf-8")
    (bundled / "config.yaml").write_text(
        "theme: dark\ncrash_windows:\n  - \"20250401-0409\"\n  - \"20260320-0324\"\n",
        encoding="utf-8",
    )
    (root / "config.yaml").write_text(
        "theme: light\ncrash_windows:\n  - \"20250401-0409\"\n",
        encoding="utf-8",
    )
    refresh_shipped_data(bundled, root)
    assert (root / "cache" / "baselines.json").read_text(encoding="utf-8") == '{"n":2}\n'
    text = (root / "config.yaml").read_text(encoding="utf-8")
    assert "theme: light" in text
    assert "20260320-0324" in text


def test_refresh_skips_identical_bytes(tmp_path: Path):
    """缓存和日期段都没变时，文件内容保持原样。"""
    bundled = tmp_path / "bundled"
    root = tmp_path / "root"
    (bundled / "cache").mkdir(parents=True)
    (root / "cache").mkdir(parents=True)
    payload = '{"n":1}\n'
    windows = "theme: dark\ncrash_windows:\n  - \"20250401-0409\"\n"
    (bundled / "cache" / "baselines.json").write_text(payload, encoding="utf-8")
    (root / "cache" / "baselines.json").write_text(payload, encoding="utf-8")
    (bundled / "config.yaml").write_text(windows, encoding="utf-8")
    (root / "config.yaml").write_text(windows, encoding="utf-8")
    refresh_shipped_data(bundled, root)
    assert (root / "cache" / "baselines.json").read_text(encoding="utf-8") == payload
    assert (root / "config.yaml").read_text(encoding="utf-8") == windows
