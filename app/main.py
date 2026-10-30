"""桌面窗口入口。

有缓存时先画基底，再在后台拉行情。没有缓存时先显示正在计算，算完再画。
行情线程只通过 evaluate_js 把状态推进页面。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any

from app.paths import bundle_root, prepare_runtime

prepare_runtime()

import webview

from app.config import AppConfig, load_config, refresh_label, save_theme
from app.count import THRESHOLDS
from app.history import load_cached
from app.live import LivePoller


class MeterApp:
    """窗口后面的状态。js_api 只暴露 get_state 和 set_theme。"""

    def __init__(self, config: AppConfig) -> None:
        """输入：已加载的配置。
        输出：尚未打开窗口的应用状态。
        用途：准备基底缓存和实时轮询共用的状态字典。
        边界：这里不打开行情库，也不拉取实时行情。
        """
        self._config = config
        self._lock = threading.Lock()
        self._window: Any = None
        self._workers_started = False
        self._saw_quote = False
        self._poller: LivePoller | None = None
        self._seq = 1
        cached = load_cached(config)
        self._state = _initial_state(config, cached)
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
        self._config = save_theme(self._config, str(theme))
        with self._lock:
            self._state["theme"] = self._config.theme
            self._bump()
        self._push()
        return self._config.theme

    def stop(self) -> None:
        """输入：无。
        输出：无。
        用途：窗口关闭后停止轮询，并结束整个进程。
        边界：行情库线程和通达信线程池不是守护线程，普通 return 停不掉，所以这里直接退出进程。
        """
        if self._poller is not None:
            self._poller.stop()
        _force_exit()

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
        用途：启动 2 分钟行情轮询。
        边界：已有轮询器时不重复启动。
        """
        if self._poller is not None:
            return
        self._poller = LivePoller(
            self._config.refresh_seconds,
            self._on_quote,
            self._on_quote_error,
            self._config.quote_host,
            self._config.quote_port,
            self._config.quote_timeout,
        )
        self._poller.start()

    def _on_quote(self, counts: list[int], universe: int, when: datetime) -> None:
        """输入：11 档家数、有效股票数、取数时刻。
        输出：无。
        用途：把实时家数写进状态并推到页面。
        边界：档数不足 11 时按现有档位就近填入，不补造家数。
        """
        with self._lock:
            for band, value in zip(self._state["bands"], counts):
                band["live"] = int(value)
            self._state["universe"] = int(universe)
            self._state["clock"] = when.strftime("%H:%M:%S")
            self._state["stale"] = False
            self._state["message"] = ""
            self._saw_quote = True
            self._bump()
        self._push()

    def _on_quote_error(self, exc: Exception) -> None:
        """输入：行情异常。
        输出：无。
        用途：保留上一帧家数，并把面板标成中断。
        边界：从未成功取过数时，文案是「行情中断」。
        """
        print(f"行情中断: {exc}")
        with self._lock:
            self._state["stale"] = True
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
    用途：打开 880×640 的计量器窗口。
    边界：配置不合法时抛出 ValueError，不创建窗口。
    """
    config = load_config()
    app = MeterApp(config)
    html = bundle_root() / "web" / "index.html"
    window = webview.create_window(
        "狗屎市场",
        html.as_uri(),
        js_api=_Api(app),
        width=880,
        height=640,
        resizable=False,
        background_color="#FFFDF8" if config.theme == "light" else "#0E0C09",
        text_select=False,
    )
    app.attach(window)
    window.events.closing += _force_exit
    window.events.closed += _force_exit
    threading.Thread(
        target=_exit_when_closing,
        args=(window.events.closing,),
        name="meter-exit",
        daemon=True,
    ).start()
    window.events.loaded += app.on_loaded
    webview.start()
    _force_exit()
    return 0


class _Api:
    """只把两个方法暴露给页面。"""

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


def _initial_state(config: AppConfig, cached: dict[str, Any] | None) -> dict[str, Any]:
    """输入：配置和可能为空的缓存。
    输出：页面状态。
    用途：有缓存时直接带上基底；没有缓存时只等实时行情。
    边界：不打开行情库，不使用施工包里的示例家数。
    """
    state = {
        "theme": config.theme,
        "computing": False,
        "stale": False,
        "clock": "",
        "universe": None,
        "message": "同步中",
        "refresh_label": refresh_label(config.refresh_seconds),
        "half_width": config.half_width_px,
        "inset": config.contour_inset,
        "bands": [{"th": th, "base": 0, "live": 0} for th in THRESHOLDS],
        "crashes": [],
    }
    if cached is not None:
        _apply_baselines(state, cached)
    return state


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
