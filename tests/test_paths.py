"""历史缓存留在包内，配置里的日期段与它对齐。"""

from pathlib import Path

from app.config import load_config
from app.paths import bundle_root, cache_file, install_user_config


def test_cache_file_stays_in_bundle(tmp_path: Path):
    """无论配置放在哪，读取的都是包内缓存，不在配置旁边另存一份。"""
    path = tmp_path / "config.yaml"
    path.write_text(
        "refresh_seconds: 120\n"
        "min_codes: 4500\n"
        "theme: dark\n"
        "quote_host: \"127.0.0.1\"\n"
        "quote_port: 7709\n"
        "quote_timeout: 8\n"
        "crash_windows:\n"
        "  - \"20250103-0113\"\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.cache_path == cache_file()
    assert config.cache_path == bundle_root() / "cache" / "baselines.json"
    assert not (tmp_path / "cache" / "baselines.json").exists()


def test_install_user_config_keeps_theme_and_skips_cache(tmp_path: Path):
    """日期段跟上包内配置，主题保留。旁边不产生缓存文件。"""
    bundled = tmp_path / "bundled"
    root = tmp_path / "root"
    bundled.mkdir()
    root.mkdir()
    (bundled / "config.yaml").write_text(
        "theme: dark\ncrash_windows:\n  - \"20250401-0409\"\n  - \"20260320-0324\"\n",
        encoding="utf-8",
    )
    (root / "config.yaml").write_text(
        "theme: light\ncrash_windows:\n  - \"20250401-0409\"\n",
        encoding="utf-8",
    )
    install_user_config(bundled / "config.yaml", root / "config.yaml")
    text = (root / "config.yaml").read_text(encoding="utf-8")
    assert "theme: light" in text
    assert "20260320-0324" in text
    assert not (root / "cache").exists()
