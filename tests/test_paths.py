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
        "quote_hosts:\n"
        "  - host: \"127.0.0.1\"\n"
        "    port: 7709\n"
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


def test_install_config_converts_single_host_once_and_preserves_preferences(tmp_path: Path):
    """输入：单地址配置与发布地址池；输出：有序列表；用途：EXE直接升级可用；边界：不重复补地址。"""
    import yaml

    source = tmp_path / "default.yaml"
    target = tmp_path / "config.yaml"
    source.write_text(
        'quote_hosts:\n  - host: "primary"\n    port: 7709\n  - host: "backup"\n    port: 7709\n',
        encoding="utf-8",
    )
    target.write_text('theme: light\nrefresh_seconds: 180\nquote_host: "custom"\nquote_port: 7710\n# 保留的注释\n', encoding="utf-8")
    install_user_config(source, target)
    settings = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert settings["quote_hosts"] == [
        {"host": "custom", "port": 7710},
        {"host": "primary", "port": 7709},
        {"host": "backup", "port": 7709},
    ]
    assert "quote_host" not in settings and "quote_port" not in settings
    assert settings["theme"] == "light" and settings["refresh_seconds"] == 180
    assert "# 保留的注释" in target.read_text(encoding="utf-8")
    before = target.read_bytes()
    install_user_config(source, target)
    assert target.read_bytes() == before


def test_install_preserves_existing_host_list(tmp_path: Path):
    """输入：已手改的多地址列表；输出：保持原地址和顺序；边界：删除废弃的单地址键。"""
    import yaml

    source = tmp_path / "default.yaml"
    target = tmp_path / "config.yaml"
    source.write_text('quote_hosts:\n  - host: "default"\n    port: 7709\n', encoding="utf-8")
    target.write_text('quote_hosts:\n  - host: "my-backup"\n    port: 7710\nquote_host: obsolete\nquote_port: 7709\n', encoding="utf-8")
    install_user_config(source, target)
    settings = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert settings == {"quote_hosts": [{"host": "my-backup", "port": 7710}]}


def test_first_launch_installs_complete_config(tmp_path: Path):
    """输入：不存在的EXE配置；输出：完整复制发布配置；边界：父目录由安装过程创建。"""
    source = tmp_path / "default.yaml"
    target = tmp_path / "runtime" / "config.yaml"
    source.write_text('quote_hosts:\n  - host: "primary"\n    port: 7709\n', encoding="utf-8")
    install_user_config(source, target)
    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_date_list_replacement_handles_legal_yaml_layouts(tmp_path: Path):
    """输入：注释、EOF、行内及缺省日期列表；输出：完全替换；边界：不保留旧日期且主题不变。"""
    import yaml
    source, target = tmp_path / "default.yaml", tmp_path / "config.yaml"
    source.write_text('crash_windows: ["20250103-0113", "20250401-0409"]\n', encoding="utf-8")
    variants = [
        'theme: light\ncrash_windows:\n  - "20260717-0730"\n  # 日期注释\n  - "20220927-1014"\n',
        'theme: light\ncrash_windows:\n  - "20260717-0730"\n  - "20220927-1014"',
        'crash_windows: ["20260717-0730"] # 日期\ntheme: light\n',
        '"crash_windows": ["20260717-0730"]\ntheme: light\n',
        '{theme: light, crash_windows: ["20260717-0730"]}',
        'theme: light\n',
    ]
    for text in variants:
        target.write_text(text, encoding="utf-8")
        install_user_config(source, target)
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert data == {"theme": "light", "crash_windows": ["20250103-0113", "20250401-0409"]}


def test_failed_config_upgrade_keeps_previous_file(tmp_path: Path, monkeypatch):
    """输入：替换文件失败；输出：原配置完整保留；边界：不留下临时文件。"""
    import pytest
    source, target = tmp_path / "default.yaml", tmp_path / "config.yaml"
    source.write_text('crash_windows: ["20250401-0409"]\n', encoding="utf-8")
    target.write_text('theme: dark\ncrash_windows: ["20250103-0113"]\n', encoding="utf-8")
    before = target.read_bytes()
    monkeypatch.setattr("app.storage.os.replace", lambda *args: (_ for _ in ()).throw(PermissionError("busy")))
    with pytest.raises(PermissionError):
        install_user_config(source, target)
    assert target.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
