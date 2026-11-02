"""前端行为验收：等比居中、刷新设置桥接和虚线控制点范围，不连接真实行情。"""

from pathlib import Path
import json

import pytest

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    """输入：无；输出：浏览器实例；用途：共用浏览器进程降低验收开销；边界：本机需安装Edge。"""
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, channel="msedge")
        yield browser
        browser.close()


@pytest.fixture
def state():
    """输入：真实基底缓存；输出：可复用页面状态；用途：覆盖当前八条股灾轮廓；边界：不连接行情服务器。"""
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "cache" / "baselines.json").read_text(encoding="utf-8"))
    return {
        "seq": 1, "theme": "dark", "refresh_seconds": 120,
        "baseline_status": "ready", "baseline_message": "", "refreshing": False,
        "universe": 5575, "clock": "11:30:00", "message": "", "stale": False, "alert": "",
        "half_width": 300, "inset": .9,
        "bands": [dict(band, live=0) for band in payload["bands"]],
        "crashes": payload["crashes"],
    }


@pytest.fixture
def page(browser, state, request):
    """输入：浏览器、真实缓存状态与可选DPR；输出：独立页面；用途：隔离状态验收；边界：每项测试清理浏览器上下文。"""
    context = browser.new_context(device_scale_factor=getattr(request, "param", 1))
    tab = context.new_page()
    tab.goto((Path(__file__).resolve().parents[1] / "web" / "index.html").as_uri())
    tab.evaluate("state => window.meter.apply(state)", state)
    yield tab
    context.close()


@pytest.mark.parametrize("page", [1, 1.25, 1.5, 2], indirect=True)
@pytest.mark.parametrize("width,height", [(816, 530), (951, 617), (640, 480), (1400, 500), (600, 1000)])
def test_canvas_fits_and_centers_at_every_aspect_ratio(page, width, height):
    """输入：不同客户区宽高；输出：断言；用途：验证全画布等比居中无裁剪；边界：含横长和竖长视口。"""
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_function(
        """() => {
            const box = document.querySelector('#meter').getBoundingClientRect();
            return Math.abs(box.width - Math.min(innerWidth / 816, innerHeight / 530) * 816) < .1;
        }"""
    )
    canvas = page.locator("#meter").bounding_box()
    scale = min(width / 816, height / 530)
    assert canvas["width"] == pytest.approx(816 * scale, abs=.1)
    assert canvas["height"] == pytest.approx(530 * scale, abs=.1)
    assert canvas["x"] == pytest.approx((width - canvas["width"]) / 2, abs=.1)
    assert canvas["y"] == pytest.approx((height - canvas["height"]) / 2, abs=.1)
    assert page.locator(".readout").count() == 11
    for readout in page.locator(".readout").all():
        box = readout.bounding_box()
        assert box["x"] >= 0
        assert box["y"] >= 0
        assert box["x"] + box["width"] <= width + .1
        assert box["y"] + box["height"] <= height + .1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight")


def test_refresh_choices_call_backend_and_confirm_selection(page):
    """输入：五档刷新选择；输出：断言；用途：验证秒数传到后端并确认选择；边界：用假桥避免真实配置写入。"""
    page.evaluate("""() => {
        window.savedIntervals = [];
        window.pywebview = {api: {set_refresh_seconds: async (seconds) => {
            window.savedIntervals.push(seconds); return seconds;
        }}};
    }""")
    assert page.locator(".js-refresh option").evaluate_all("options => options.map(option => option.value)") == ["60", "120", "180", "240", "300"]
    for seconds in (60, 180, 240, 300, 120):
        page.select_option(".js-refresh", str(seconds))
        page.wait_for_function("!document.querySelector('.js-refresh').disabled")
        assert page.locator(".js-refresh").input_value() == str(seconds)
    assert page.evaluate("window.savedIntervals") == [60, 180, 240, 300, 120]


def test_failed_refresh_save_restores_confirmed_selection(page):
    """输入：后端拒绝保存；输出：断言；用途：保证失败不显示虚假的刷新间隔；边界：保留上次成功的120秒。"""
    page.evaluate("""() => {
        window.pywebview = {api: {set_refresh_seconds: async () => {throw new Error('磁盘只读');}}};
    }""")
    page.select_option(".js-refresh", "300")
    page.wait_for_function("!document.querySelector('.js-refresh').disabled")
    assert page.locator(".js-refresh").input_value() == "120"
    assert page.locator(".js-settingsError").is_visible()
    assert "磁盘只读" in page.locator(".js-settingsError").inner_text()


def test_failed_theme_save_rolls_back_and_reports_error(page):
    """输入：主题保存失败；输出：断言；用途：验证失败回滚且无未处理异常；边界：保留已确认dark主题。"""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.evaluate("window.pywebview={api:{set_theme:async()=>{throw Error('磁盘只读')}}}")
    page.locator(".js-theme").click()
    page.wait_for_function("!document.querySelector('.js-theme').disabled")
    assert page.locator("#meter").get_attribute("data-theme") == "dark"
    assert "主题未保存" in page.locator(".js-settingsError").inner_text()
    assert not errors


def test_pending_settings_survive_pushes_and_out_of_order_responses(page, state):
    """输入：并发主题/间隔保存和旧设置推送；输出：断言；用途：保护待保存选择；边界：两个请求以相反次序完成。"""
    page.evaluate("""() => {
        window.calls={theme:0,refresh:0};
        window.pywebview={api:{
            set_theme:()=>{window.calls.theme++;return new Promise(resolve=>window.finishTheme=resolve)},
            set_refresh_seconds:()=>{window.calls.refresh++;return new Promise(resolve=>window.finishRefresh=resolve)}
        }};
    }""")
    page.locator(".js-theme").click()
    page.select_option(".js-refresh", "300")
    page.evaluate("document.querySelector('.js-theme').dispatchEvent(new Event('click'));document.querySelector('.js-refresh').dispatchEvent(new Event('change'))")
    page.evaluate("state=>window.meter.apply({...state,seq:2})", state)
    assert page.locator("#meter").get_attribute("data-theme") == "light"
    assert page.locator(".js-refresh").input_value() == "300"
    assert page.locator(".js-theme").is_disabled()
    assert page.locator(".js-refresh").is_disabled()
    page.evaluate("window.finishRefresh(300)")
    page.wait_for_function("!document.querySelector('.js-refresh').disabled")
    page.evaluate("window.finishTheme('light')")
    page.wait_for_function("!document.querySelector('.js-theme').disabled")
    assert page.locator("#meter").get_attribute("data-theme") == "light"
    assert page.locator(".js-refresh").input_value() == "300"
    assert page.evaluate("window.calls") == {"theme": 1, "refresh": 1}


def test_old_and_duplicate_states_cannot_revert_animation(page, state):
    """输入：快速递增、重复和落后序号；输出：断言；用途：防止旧动画或旧快照覆写；边界：目标回到先前完成值。"""
    page.evaluate("""state=>{
        window.meter.apply({...state,seq:2,bands:state.bands.map(b=>({...b,live:100}))});
        window.meter.apply({...state,seq:3});
        window.meter.apply({...state,seq:3,bands:state.bands.map(b=>({...b,live:200}))});
        window.meter.apply({...state,seq:2,bands:state.bands.map(b=>({...b,live:300}))});
    }""", state)
    page.wait_for_timeout(350)
    assert page.locator(".row__value").all_text_contents() == ["0"] * 11


@pytest.mark.parametrize("status", ["missing", "mismatch", "invalid", "zero_base"])
def test_baseline_warning_survives_live_updates_without_fake_bars(page, state, status):
    """输入：基底异常与成功行情；输出：断言；用途：保留独立降级提示及数字；边界：异常缓存即使带宽度也不画比例柱。"""
    state.update(seq=2, baseline_status=status, baseline_message="历史基底不可用，暂仅显示家数")
    state["bands"] = [dict(band, live=100) for band in state["bands"]]
    page.evaluate("state=>window.meter.apply(state)", state)
    page.wait_for_timeout(300)
    page.evaluate("state=>window.meter.apply({...state,seq:3,message:''})", state)
    assert page.locator(".js-baseline").is_visible()
    assert "暂仅显示家数" in page.locator(".js-baseline").inner_text()
    assert page.locator(".row__value").all_text_contents() == ["100"] * 11
    assert page.locator(".row__fill").evaluate_all("items=>items.every(el=>getComputedStyle(el).display==='none')")
    assert page.locator(".contour").count() == 0


def test_waiting_failure_and_retry_leave_settings_operable(page, state):
    """输入：首次等待、失败与重新取数；输出：断言；用途：验证顶部操作可达和失败停转；边界：重试忙碌时禁用重复提交。"""
    page.evaluate("window.retryCalls=0;window.pywebview={api:{refresh_now:async()=>{window.retryCalls++;return true}}}")
    page.evaluate("state=>window.meter.apply({...state,seq:2,universe:null,refreshing:true})", state)
    assert page.locator(".meter__veil").is_visible()
    assert page.locator(".js-retry").is_disabled()
    assert page.locator(".js-refresh").evaluate("el=>{const b=el.getBoundingClientRect();return document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)===el}")
    page.evaluate("state=>window.meter.apply({...state,seq:3,universe:null,refreshing:false,stale:true,alert:'网络连接失败'})", state)
    assert not page.locator(".meter__spin").is_visible()
    assert "重试" in page.locator(".meter__veilText").inner_text()
    page.locator(".js-retry").click()
    assert page.evaluate("window.retryCalls") == 1
    page.evaluate("state=>window.meter.apply({...state,seq:4,universe:null,refreshing:true})", state)
    assert page.locator(".js-retry").is_disabled()


def test_zero_valid_universe_stops_waiting_and_allows_retry(page, state):
    """输入：完整行情但零有效成交；输出：断言；用途：消除无止境蒙板且提供恢复入口；边界：不伪造有效股票或涨幅。"""
    page.evaluate("state=>window.meter.apply({...state,seq:2,universe:0,refreshing:false})", state)
    assert not page.locator(".meter__veil").is_visible()
    assert page.locator(".js-meta").inner_text() == "暂无有效成交"
    assert page.locator(".js-retry").is_visible()
    assert not page.locator(".js-retry").is_disabled()


def test_bridge_failure_recovers_on_next_valid_state(page, state):
    """输入：初始状态请求失败及后续正常推送；输出：断言；用途：验证错误可恢复；边界：不清除其它设置保存错误。"""
    page.evaluate("""() => {
        window.pywebview={api:{get_state:async()=>{throw Error('连接失效')}}};
        window.dispatchEvent(new Event('pywebviewready'));
    }""")
    page.wait_for_function("!document.querySelector('.js-settingsError').hidden")
    assert "状态获取失败" in page.locator(".js-settingsError").inner_text()
    page.evaluate("state=>window.meter.apply({...state,seq:2})", state)
    assert not page.locator(".js-settingsError").is_visible()


def test_all_real_cache_labels_remain_hoverable(page, state):
    """输入：真实八段历史缓存；输出：断言；用途：验证标签可达、提示家数与缩放不裁切；边界：标签按峰值排序而非配置顺序。"""
    assert page.locator(".chip").count() == len(state["crashes"]) == 8
    by_key = {crash["id"]: crash for crash in state["crashes"]}
    for chip in page.locator(".chip").all():
        chip.hover()
        page.wait_for_timeout(170)
        assert "is-hot" in chip.get_attribute("class")
        assert str(by_key[chip.get_attribute("data-key")]["worst"][-1]) in chip.inner_text().replace(",", "")
        assert chip.bounding_box()["x"] >= 0


def test_compact_canvas_removes_outer_copy_and_margins(page):
    """输入：紧凑画布页面；输出：断言；用途：验证四周裁剪与底部文字删除；边界：保留标题副标题和全部图表。"""
    assert page.locator(".meter__overline, .meter__footer, .js-refreshFooter").count() == 0
    assert "A-SHARE" not in page.locator("body").inner_text()
    assert "基底 ·" not in page.locator("body").inner_text()
    assert "填充 ·" not in page.locator("body").inner_text()
    assert page.evaluate("document.querySelector('.meter__header').offsetLeft") == 8
    assert page.evaluate("document.querySelector('.meter__header').offsetTop") == 4
    assert page.evaluate("document.querySelector('.meter__chart').offsetTop") == 90


def test_contour_controls_stay_inside_adjacent_band_ranges(page):
    """输入：零值、平台和陡增半宽；输出：断言；用途：避免曲线过冲/过轴伪造宽度；边界：盖坐标传入首末档之内。"""
    results = page.evaluate("""() => [
        [0, 0, 0, 0, 0, 0, 1, 1, 1, 270, 270],
        [94, 99, 103, 106, 108, 115, 222, 257, 262, 267, 270],
    ].map(halves => ({halves, path: buildContour(halves, 46, 384)}))""")
    import re

    for result in results:
        values = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", result["path"])]
        start = values[:2]
        for index in range(2, len(values), 6):
            c1x, c1y, c2x, c2y, endx, endy = values[index:index + 6]
            assert min(start[0], endx) - .11 <= c1x <= max(start[0], endx) + .11
            assert min(start[0], endx) - .11 <= c2x <= max(start[0], endx) + .11
            assert min(start[1], endy) - .11 <= c1y <= max(start[1], endy) + .11
            assert min(start[1], endy) - .11 <= c2y <= max(start[1], endy) + .11
            start = [endx, endy]
        assert min(values[1::2]) < 45
        assert max(values[1::2]) > 385
