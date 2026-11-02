"""桌面窗口入口。

有缓存时先画基底，再在后台拉行情。历史缓存由离线重算生成。
行情线程只通过 evaluate_js 把状态推进页面。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from app.paths import bundle_root, prepare_runtime, runtime_root

import webview

from app.config import AppConfig, load_config, refresh_label, save_refresh_seconds, save_theme
from app.count import THRESHOLDS
from app.baseline_cache import diagnose_cache
from app.live import LivePoller, Snapshot, describe_quote_error
from app.window_layout import center_window


class MeterApp:
    """窗口状态、可持久化设置和单线程行情轮询。"""

    def __init__(self, config: AppConfig) -> None:
        """输入：已加载的配置。
        输出：尚未打开窗口的应用状态。
        用途：准备基底缓存和实时轮询共用的状态字典。
        边界：这里不打开行情库，也不拉取实时行情。
        """
        self._config = config
        self._lock = threading.Lock()
        self._settings_lock = threading.Lock()
        self._window: Any = None
        self._workers_started = False
        self._saw_quote = False
        self._poller: LivePoller | None = None
        self._seq = 1
        cached, baseline_status, baseline_message = diagnose_cache(config)
        self._state = _initial_state(config, cached)
        self._state["baseline_status"] = baseline_status
        self._state["baseline_message"] = baseline_message
        self._state["seq"] = self._seq

    def attach(self, window: Any) -> None:
        """输入：pywebview 窗口。
        输出：无。
        用途：让后台线程能够把状态推到页面。
        边界：重复附加时以最后一次为准。
        """
        self._window = window

    def on_loaded(self) -> None:
        """输入：无。
        输出：无。
        用途：页面加载完成后推送当前状态，并开始计算或轮询。
        边界：窗口还不存在时不推送。
        """
        self._ensure_workers()
        self._push()

    def get_state(self) -> dict[str, Any]:
        """输入：无。
        输出：页面可直接应用的状态字典。
        用途：页面就绪时拉取第一帧。
        边界：会启动尚未启动的计算或轮询。返回的是副本。
        """
        self._ensure_workers()
        with self._lock:
            return json.loads(json.dumps(self._state, ensure_ascii=False))

    def set_theme(self, theme: str) -> str:
        """输入：dark 或 light。
        输出：写入配置后的主题名。
        用途：顶栏按钮切换明暗并记住选择。
        边界：非法主题抛出 ValueError，不改文件。
        """
        with self._settings_lock:
            self._config = save_theme(self._config, str(theme))
            with self._lock:
                self._state["theme"] = self._config.theme
                self._bump()
            saved = self._config.theme
        self._push()
        return saved

    def set_refresh_seconds(self, seconds: int) -> int:
        """输入：1～5 分钟对应的整数秒数；输出：保存后的秒数。
        用途：同步配置、实际轮询周期与页面状态。
        边界：非法值或写入失败时不改变轮询；并发主题切换串行保存。
        """
        with self._settings_lock:
            self._config = save_refresh_seconds(self._config, seconds)
            if self._poller is not None:
                self._poller.set_interval(seconds)
            with self._lock:
                self._state["refresh_seconds"] = seconds
                self._state["refresh_label"] = refresh_label(seconds)
                self._bump()
        self._push()
        return seconds

    def stop(self) -> None:
        """输入：无。
        输出：无。
        用途：窗口关闭后停止轮询，并结束整个进程。
        边界：先取消轮询，再结束原生窗口进程；持久化文件使用原子替换保护。
        """
        if self._poller is not None:
            self._poller.stop()
        _force_exit()

    def refresh_now(self) -> bool:
        """输入：无；输出：是否已接受立即取数。
        用途：连接失败或等待时允许手动重试。
        边界：正在请求时拒绝重复提交；尚未启动时只启动第一轮，不额外排队。
        """
        with self._lock:
            started = self._workers_started
        if not started:
            self._ensure_workers()
            return True
        with self._lock:
            if self._state["refreshing"] or self._poller is None:
                return False
            self._state["refreshing"] = True
            self._bump()
        self._poller.request_refresh()
        self._push()
        return True

    def _on_refreshing(self, active: bool) -> None:
        """输入：是否正在取数；输出：无。
        用途：同步重试按钮和等待状态；边界：状态未变化时不推重复帧。
        """
        with self._lock:
            if self._state["refreshing"] == active:
                return
            self._state["refreshing"] = active
            self._bump()
        self._push()

    def _ensure_workers(self) -> None:
        """输入：无。
        输出：无。
        用途：缓存缺失时不重算，只开始行情轮询。
        边界：只启动一次。不读取 source_db。
        """
        with self._lock:
            if self._workers_started:
                return
            self._workers_started = True
        self._start_live()

    def _start_live(self) -> None:
        """输入：无。
        输出：无。
        用途：按配置的间隔启动行情轮询。
        边界：已有轮询器时不重复启动。
        """
        with self._settings_lock:
            if self._poller is not None:
                return
            self._poller = LivePoller(
                self._config.refresh_seconds,
                self._on_quote,
                self._on_quote_error,
                self._config.quote_hosts,
                self._config.quote_timeout,
                min_stocks=self._config.quote_min_stocks,
                min_coverage=self._config.quote_min_coverage,
                deadline_seconds=self._config.quote_deadline_seconds,
                on_refreshing=self._on_refreshing,
            )
            self._poller.start()

    def _on_quote(self, counts: list[int], universe: int, when: datetime) -> None:
        """输入：11 档家数、有效股票数、取数时刻。
        输出：无。
        用途：把实时家数写进状态并推到页面。
        边界：必须为11档非负整数且符合累计关系；非法快照不改变上一帧。
        """
        if len(counts) != len(THRESHOLDS) or any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("实时家数必须是11档非负整数")
        if any(a > b for a, b in zip(counts, counts[1:])) or universe < max(counts):
            raise ValueError("实时家数不符合累计口径")
        snapshot = getattr(self._poller, "last_snapshot", None)
        with self._lock:
            for band, value in zip(self._state["bands"], counts):
                band["live"] = int(value)
            self._state["universe"] = int(universe)
            self._state["clock"] = when.strftime("%H:%M:%S")
            self._state["stale"] = False
            self._state["alert"] = ""
            self._state["message"] = "暂无有效成交" if universe == 0 else ""
            if isinstance(snapshot, Snapshot):
                self._state["raw_stocks"] = snapshot.raw_stocks
                self._state["active_host"] = f"{snapshot.host.host}:{snapshot.host.port}"
            self._saw_quote = True
            self._bump()
        self._push()

    def _on_quote_error(self, exc: Exception) -> None:
        """输入：行情异常。
        输出：无。
        用途：保留上一帧家数，并在窗口右上角亮出红窗。
        边界：从未成功取过数时，副标题仍是「行情中断」。下一轮成功会清掉红窗。
        """
        logging.getLogger("meter").warning("行情中断：%s", exc)
        with self._lock:
            self._state["stale"] = True
            self._state["alert"] = describe_quote_error(exc)
            if not self._saw_quote:
                self._state["message"] = "行情中断"
            self._bump()
        self._push()

    def _bump(self) -> None:
        """输入：无。调用方必须已持有状态锁。
        输出：无。
        用途：给状态一个递增序号，页面忽略迟到的旧帧。
        边界：不推送界面。
        """
        self._seq += 1
        self._state["seq"] = self._seq

    def _push(self) -> None:
        """输入：无。
        输出：无。
        用途：把当前状态交给页面的 meter.apply。
        边界：窗口未就绪或页面还没装上计量器时忽略推送失败。
        """
        window = self._window
        if window is None:
            return
        with self._lock:
            payload = json.dumps(self._state, ensure_ascii=False)
        try:
            window.evaluate_js(f"window.meter && window.meter.apply({payload})")
        except Exception:
            return


def _force_exit() -> None:
    """输入：无。
    输出：无。进程不会返回。
    用途：点关闭后立刻结束行情线程，不等 WebView2 拆除窗口类。
    边界：在界面线程上调用。os._exit 在这个线程里不会中断拆除，所以直接结束进程。
    """
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.TerminateProcess.restype = ctypes.c_int
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), 0)
    os._exit(0)


def _exit_when_closing(closing) -> None:
    """输入：窗口正在关闭的事件。
    输出：无。进程不会返回。
    用途：点关闭后，即使 WebView2 拆除失败，也结束行情线程。
    边界：在窗口创建后、消息循环开始前启动。
    """
    closing.wait()
    _force_exit()


def main() -> int:
    """输入：无。
    输出：进程退出码，正常关闭为 0。
    用途：打开客户区为 816×530 的可缩放窗口，并在显示器工作区居中。
    边界：配置或资源不合法时显示错误并返回1，不创建主窗口。
    """
    _configure_logging()
    try:
        prepare_runtime()
        config = load_config()
        app = MeterApp(config)
        html = bundle_root() / "web" / "index.html"
        if not html.is_file():
            raise FileNotFoundError(f"缺少界面资源：{html}")
        icon = bundle_root() / "web" / "assets" / "app.ico"
        if not icon.is_file():
            raise FileNotFoundError(f"缺少窗口图标：{icon}")
    except Exception as exc:
        logging.getLogger("meter").exception("启动失败")
        _show_startup_error(exc)
        return 1
    window = webview.create_window(
        "狗屎市场",
        html.as_uri(),
        js_api=_Api(app),
        width=816,
        height=530,
        resizable=True,
        min_size=(408, 300),
        background_color="#FFFDF8" if config.theme == "light" else "#0E0C09",
        text_select=False,
    )
    app.attach(window)
    window.events.shown += lambda: center_window(window, client_size=(816, 530))
    window.events.closing += _force_exit
    window.events.closed += _force_exit
    threading.Thread(
        target=_exit_when_closing,
        args=(window.events.closing,),
        name="meter-exit",
        daemon=True,
    ).start()
    window.events.loaded += app.on_loaded
    webview.start(icon=str(icon))
    _force_exit()
    return 0


class _Api:
    """仅向页面暴露状态、主题、刷新间隔设置和立即取数。"""

    def __init__(self, app: MeterApp) -> None:
        """输入：应用状态。
        输出：页面桥。
        用途：限制 pywebview 能调用的方法。
        边界：不复制应用状态。
        """
        self._app = app

    def get_state(self) -> dict[str, Any]:
        """输入：无。
        输出：当前状态。
        用途：页面加载时取第一帧。
        边界：与 MeterApp.get_state 相同。
        """
        return self._app.get_state()

    def set_theme(self, theme: str) -> str:
        """输入：dark 或 light。
        输出：保存后的主题。
        用途：顶栏按钮写回配置。
        边界：非法取值抛出 ValueError。
        """
        return self._app.set_theme(theme)

    def set_refresh_seconds(self, seconds: int) -> int:
        """输入：刷新整数秒数；输出：保存后的秒数。
        用途：下拉菜单更新轮询周期；边界：非法值由应用层拒绝。
        """
        return self._app.set_refresh_seconds(seconds)

    def refresh_now(self) -> bool:
        """输入：无；输出：是否接受重试；用途：页面请求立即取数；边界：不产生并行请求。"""
        return self._app.refresh_now()


def _initial_state(config: AppConfig, cached: dict[str, Any] | None) -> dict[str, Any]:
    """输入：配置和可能为空的缓存。
    输出：页面状态。
    用途：有缓存时直接带上基底；没有缓存时只等实时行情。
    边界：不打开行情库，不使用施工包里的示例家数。
    """
    state = {
        "theme": config.theme,
        "computing": False,
        "refreshing": False,
        "baseline_status": "missing",
        "baseline_message": "历史基底不可用，请重新计算缓存",
        "raw_stocks": None,
        "active_host": "",
        "stale": False,
        "alert": "",
        "clock": "",
        "universe": None,
        "message": "同步中",
        "refresh_label": refresh_label(config.refresh_seconds),
        "refresh_seconds": config.refresh_seconds,
        "half_width": config.half_width_px,
        "inset": config.contour_inset,
        "bands": [{"th": th, "base": 0, "live": 0} for th in THRESHOLDS],
        "crashes": [],
    }
    if cached is not None:
        _apply_baselines(state, cached)
    return state


def _configure_logging() -> None:
    """输入：无；输出：无。
    用途：无控制台EXE也记录故障，日志滚动保留最多约3MB。
    边界：目录不可写时不阻止后续显示启动错误；不重复安装处理器。
    """
    logger = logging.getLogger("meter")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return
    try:
        folder = runtime_root() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / "meter.log", maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())


def _show_startup_error(exc: Exception) -> None:
    """输入：启动异常；输出：无。
    用途：配置损坏等错误在无控制台EXE中可见。
    边界：Windows用错误对话框，其它平台使用标准错误；不修改用户配置。
    """
    message = f"程序启动失败：{exc}\n\n请检查程序旁的 config.yaml 和 logs/meter.log。"
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "狗屎市场 · 启动失败", 0x10)
    else:
        import sys
        if sys.stderr is not None:
            print(message, file=sys.stderr)


def _apply_baselines(state: dict[str, Any], payload: dict[str, Any]) -> None:
    """输入：页面状态和重算结果。
    输出：无。就地更新基底和轮廓。
    用途：换基底时保留已经到来的实时家数。
    边界：缓存缺少某档时该档基底为 0。
    """
    lives = {int(band["th"]): int(band["live"]) for band in state["bands"]}
    bases = {int(item["th"]): int(item["base"]) for item in payload.get("bands") or []}
    state["bands"] = [
        {"th": th, "base": bases.get(th, 0), "live": lives.get(th, 0)}
        for th in THRESHOLDS
    ]
    state["crashes"] = payload.get("crashes") or []


if __name__ == "__main__":
    raise SystemExit(main())
