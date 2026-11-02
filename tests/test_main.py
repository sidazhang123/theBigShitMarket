"""GUI 设置桥接的集成测试，不创建窗口或连接行情。"""

from unittest.mock import Mock

import pytest

from app.config import load_config
from app.live import QuoteHostsError
from app.main import MeterApp, _Api


def test_refresh_api_persists_and_updates_active_poller(tmp_path, monkeypatch):
    """输入：下拉选择秒数；输出：文件、状态、轮询器同步；边界：非法选择不改变原状态。"""
    path = tmp_path / "config.yaml"
    path.write_text('theme: dark\nquote_hosts:\n  - host: localhost\ncrash_windows:\n  - "20250103-0113"\n', encoding="utf-8")
    monkeypatch.setattr("app.main.diagnose_cache", lambda config: (None, "missing", "缺少历史基底"))
    app = MeterApp(load_config(path))
    app._workers_started = True
    app._poller = Mock()
    window = Mock()
    app.attach(window)
    api = _Api(app)
    for seconds in (60, 120, 180, 240, 300):
        assert api.set_refresh_seconds(seconds) == seconds
        state = api.get_state()
        assert state["refresh_seconds"] == seconds
        assert state["refresh_label"] == f"{seconds // 60} min 刷新"
        assert load_config(path).refresh_seconds == seconds
        app._poller.set_interval.assert_called_with(seconds)
    count = app._poller.set_interval.call_count
    with pytest.raises(ValueError):
        api.set_refresh_seconds(90)
    assert app._poller.set_interval.call_count == count
    assert api.get_state()["refresh_seconds"] == 300
    api.set_theme("light")
    assert load_config(path).refresh_seconds == 300
    assert load_config(path).theme == "light"
    assert window.evaluate_js.call_count >= 6


def test_live_start_passes_ordered_hosts_and_failure_keeps_last_frame(tmp_path, monkeypatch):
    """输入：多端点配置和全部失败；输出：轮询顺序正确、保留上一帧并告警；边界：不连接真实行情。"""
    from datetime import datetime

    path = tmp_path / "config.yaml"
    path.write_text('quote_hosts:\n  - host: first\n  - host: second\ncrash_windows:\n  - "20250103-0113"\n', encoding="utf-8")
    config = load_config(path)
    monkeypatch.setattr("app.main.diagnose_cache", lambda config: (None, "missing", "缺少历史基底"))
    poller_class = Mock()
    monkeypatch.setattr("app.main.LivePoller", poller_class)
    app = MeterApp(config)
    app._ensure_workers()
    assert poller_class.call_args.args[3:] == (config.quote_hosts, config.quote_timeout)
    poller_class.return_value.start.assert_called_once()
    app._on_quote([7] * 11, 5000, datetime(2026, 11, 2, 10, 0))
    app._on_quote_error(QuoteHostsError([(host, TimeoutError("timed out")) for host in config.quote_hosts]))
    state = app.get_state()
    assert [band["live"] for band in state["bands"]] == [7] * 11
    assert state["universe"] == 5000 and state["clock"] == "10:00:00"
    assert state["stale"] is True
    assert "first:7709" in state["alert"] and "second:7709" in state["alert"]


def test_baseline_warning_survives_success_and_zero_volume_is_normal(tmp_path, monkeypatch):
    """输入：基底失效和完整但零成交快照；输出：独立提示保留；边界：不显示行情服务器失败。"""
    from datetime import datetime
    from app.config import QuoteHost
    from app.live import Snapshot

    path = tmp_path / "config.yaml"
    path.write_text('quote_hosts: [{host: first}]\ncrash_windows: ["20250103-0113"]\n', encoding="utf-8")
    monkeypatch.setattr("app.main.diagnose_cache", lambda config: (None, "mismatch", "历史配置不匹配，请重算"))
    app = MeterApp(load_config(path))
    app._workers_started = True
    app._poller = Mock(last_snapshot=Snapshot([0] * 11, 0, 5000, QuoteHost("first", 7709)))
    app._on_quote([0] * 11, 0, datetime.now())
    state = app.get_state()
    assert state["universe"] == 0 and state["message"] == "暂无有效成交"
    assert state["alert"] == "" and state["stale"] is False
    assert state["baseline_status"] == "mismatch" and "重算" in state["baseline_message"]
    assert state["raw_stocks"] == 5000 and state["active_host"] == "first:7709"


def test_manual_retry_is_coalesced_and_state_tracks_fetch(tmp_path, monkeypatch):
    """输入：连续重试及完成通知；输出：仅排队一轮；边界：状态序号递增并允许下一次重试。"""
    path = tmp_path / "config.yaml"
    path.write_text('quote_hosts: [{host: first}]\ncrash_windows: ["20250103-0113"]\n', encoding="utf-8")
    monkeypatch.setattr("app.main.diagnose_cache", lambda config: (None, "invalid", "缓存损坏"))
    app = MeterApp(load_config(path))
    app._workers_started = True
    app._poller = Mock()
    api = _Api(app)
    assert api.refresh_now() is True
    assert api.refresh_now() is False
    app._poller.request_refresh.assert_called_once()
    assert api.get_state()["refreshing"] is True
    app._on_refreshing(False)
    assert api.refresh_now() is True


def test_invalid_quote_does_not_replace_last_frame(tmp_path, monkeypatch):
    """输入：短数组及非累计数据；输出：错误且原状态不变；边界：不发起行情。"""
    from datetime import datetime
    path = tmp_path / "config.yaml"
    path.write_text('quote_hosts: [{host: first}]\ncrash_windows: ["20250103-0113"]\n', encoding="utf-8")
    app = MeterApp(load_config(path))
    app._workers_started = True
    before = app.get_state()
    for counts in ([1], [10] + [0] * 10, [-1] * 11):
        with pytest.raises(ValueError):
            app._on_quote(counts, 5000, datetime.now())
        assert app.get_state() == before


def test_startup_errors_are_visible_without_console(monkeypatch):
    """输入：配置初始化失败；输出：错误提示及退出码1；边界：不创建真实窗口。"""
    import app.main as module
    show = Mock()
    monkeypatch.setattr(module, "_configure_logging", lambda: None)
    monkeypatch.setattr(module, "prepare_runtime", Mock(side_effect=ValueError("配置损坏")))
    monkeypatch.setattr(module, "_show_startup_error", show)
    assert module.main() == 1
    assert "配置损坏" in str(show.call_args.args[0])


@pytest.mark.parametrize("missing_icon", [False, True])
def test_native_window_uses_bundled_icon(tmp_path, monkeypatch, missing_icon):
    """输入：资源目录及缺图标状态；输出：原生图标路径或可见启动错误；边界：不运行真实窗口或退出进程。"""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import app.main as module

    assets = tmp_path / "web" / "assets"
    assets.mkdir(parents=True)
    (assets.parent / "index.html").write_text("<html></html>", encoding="utf-8")
    icon = assets / "app.ico"
    if not missing_icon:
        icon.write_bytes(b"test-icon")
    window = MagicMock()
    start = Mock()
    show = Mock()
    create = Mock(return_value=window)
    monkeypatch.setattr(module, "bundle_root", lambda: tmp_path)
    monkeypatch.setattr(module, "_configure_logging", lambda: None)
    monkeypatch.setattr(module, "prepare_runtime", lambda: None)
    monkeypatch.setattr(module, "load_config", lambda: SimpleNamespace(theme="dark"))
    monkeypatch.setattr(module, "MeterApp", Mock())
    monkeypatch.setattr(module, "_show_startup_error", show)
    monkeypatch.setattr(module, "_force_exit", Mock())
    monkeypatch.setattr(module.threading, "Thread", Mock())
    monkeypatch.setattr(module.webview, "create_window", create)
    monkeypatch.setattr(module.webview, "start", start)

    assert module.main() == (1 if missing_icon else 0)
    if missing_icon:
        assert "缺少窗口图标" in str(show.call_args.args[0])
        create.assert_not_called()
        start.assert_not_called()
    else:
        start.assert_called_once_with(icon=str(icon))
        show.assert_not_called()
