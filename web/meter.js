/* 急跌梯度计量器。
   设计画布按视口等比居中缩放，家数、比例尺、刷新间隔和主题由页面状态推进。
   半宽像素 = round(家数 * 内缩 * halfWidth / 基底6%家数)，与 app/scale.py 一致。
   灰柱内缩为 1，虚线内缩用状态里的 inset，只乘这一次。 */

const CX = 400;
const ROW_H = 34;
const ROW_TOP0 = 28;
const ROWS_Y = Array.from({length: 11}, (_, i) => ROW_TOP0 + i * ROW_H + ROW_H / 2);
const JUMP_MS = 260;
const OVERSHOOT_MS = 120;
const OVERSHOOT_PX = 6;
const THRESHOLDS = [16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6];

function halfPx(count, baseShallow, halfWidth, inset) {
  /* 输入：家数、浅档基底、半宽与内缩；输出：整数半宽；用途：统一比例尺；边界：无基底或非正数返回零。 */
  if (baseShallow <= 0 || count <= 0 || halfWidth <= 0 || inset <= 0) return 0;
  return Math.round(count * inset * halfWidth / baseShallow);
}

function buildContour(halves, ytop, ybot, cx = CX, k = 0.8) {
  /* 输入：11 档半宽、上下盖坐标、中轴和曲线张力；输出：闭合 SVG 路径。
     用途：连接每档精确宽度，控制点限制在本段范围，避免虚构家数过冲。
     边界：半宽必须覆盖11档；上下盖始终位于首末档中心之外。 */
  ytop = Math.min(ytop, ROWS_Y[0] - 1);
  ybot = Math.max(ybot, ROWS_Y[10] + 1);
  const p = [[cx - halves[0] * 0.55, ytop], [cx + halves[0] * 0.55, ytop]];
  halves.forEach((h, i) => p.push([cx + h, ROWS_Y[i]]));
  p.push([cx + halves[10] * 0.55, ybot], [cx - halves[10] * 0.55, ybot]);
  for (let i = 10; i >= 0; i--) p.push([cx - halves[i], ROWS_Y[i]]);
  const n = p.length;
  const f = (v) => v.toFixed(1);
  const between = (value, a, b) => Math.max(Math.min(a, b), Math.min(Math.max(a, b), value));
  let d = `M${f(p[0][0])} ${f(p[0][1])}`;
  for (let i = 0; i < n; i++) {
    const p0 = p[(i - 1 + n) % n];
    const p1 = p[i];
    const p2 = p[(i + 1) % n];
    const p3 = p[(i + 2) % n];
    const c1x = between(p1[0] + (p2[0] - p0[0]) / 6 * k, p1[0], p2[0]);
    const c1y = between(p1[1] + (p2[1] - p0[1]) / 6 * k, p1[1], p2[1]);
    const c2x = between(p2[0] - (p3[0] - p1[0]) / 6 * k, p1[0], p2[0]);
    const c2y = between(p2[1] - (p3[1] - p1[1]) / 6 * k, p1[1], p2[1]);
    d += `C${f(c1x)} ${f(c1y)} ${f(c2x)} ${f(c2y)} ${f(p2[0])} ${f(p2[1])}`;
  }
  return d + "Z";
}

function hexTexture(w, h, r, pad = 10) {
  const colw = 1.5 * r;
  const rowh = Math.sqrt(3) * r;
  let d = "";
  for (let i = -2; i < w / colw + 3; i++) {
    for (let j = -2; j < h / rowh + 3; j++) {
      const cx = i * colw + 30;
      const cy = j * rowh + (i % 2 ? rowh / 2 : 0);
      const vs = [];
      for (let a = 0; a < 360; a += 60) {
        vs.push([cx + r * Math.cos(a * Math.PI / 180), cy + r * Math.sin(a * Math.PI / 180)]);
      }
      const xs = vs.map((v) => v[0]);
      const ys = vs.map((v) => v[1]);
      if (Math.min(...xs) < pad || Math.max(...xs) > w - pad) continue;
      if (Math.min(...ys) < pad || Math.max(...ys) > h - pad) continue;
      d += "M" + vs.map((v) => v.map((t) => Math.round(t)).join(" ")).join("L") + "Z";
    }
  }
  return d;
}

function jumpWidth(from, to, ms) {
  const over = to + Math.sign(to - from) * OVERSHOOT_PX;
  if (ms < OVERSHOOT_MS) {
    const k = ms / OVERSHOOT_MS;
    return from + (over - from) * (1 - Math.pow(1 - k, 3));
  }
  const k = (ms - OVERSHOOT_MS) / (JUMP_MS - OVERSHOOT_MS);
  return over + (to - over) * (0.5 - Math.cos(Math.PI * k) / 2);
}

function createMeter(root) {
  /* 输入：计量器根元素；输出：状态与主题更新接口。
     用途：建立图表和设置交互；边界：Python桥未连接时设置保存会显示错误。 */
  const round = (n) => Math.round(n);
  let seen = -1;
  let anim = 0;
  let crashes = [];
  let baseShallow = 0;
  let halfWidth = 300;
  let inset = 0.9;
  let geometrySig = "";
  let refreshSeconds = 120;
  let savingRefresh = false;
  let confirmedTheme = root.dataset.theme || "dark";
  let savingTheme = false;
  let refreshRequest = 0;
  let themeRequest = 0;
  let retryPending = false;
  let refreshing = false;
  let retryVisible = true;
  const settingErrors = new Map();

  root.innerHTML = `
    <svg class="meter__hex" viewBox="0 0 816 530" fill="none" aria-hidden="true">
      <path d="${hexTexture(816, 530, 78)}" stroke="var(--txt-3)" stroke-opacity=".16" stroke-width="1"/>
    </svg>
    <div class="meter__header">
      <div>
        <div class="meter__titleRow">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M10 2.2L16.8 6.1V13.9L10 17.8L3.2 13.9V6.1L10 2.2Z" stroke="var(--contour)" stroke-width="1.4" stroke-linejoin="round"/>
            <circle cx="10" cy="10" r="2.2" fill="var(--contour)"/>
          </svg>
          <div class="meter__title">The Big Shit Market</div>
        </div>
        <div class="meter__meta js-meta">正在计算基底</div>
      </div>
      <div class="meter__right">
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
          <circle class="js-livedot-halo" cx="5" cy="5" r="4.6" fill="var(--d06)" fill-opacity=".2"/>
          <circle class="js-livedot-core" cx="5" cy="5" r="2.6" fill="var(--d06)"/>
        </svg>
        <div class="meter__live">LIVE</div>
        <div class="meter__clock js-clock">--:--:--</div>
        <select class="meter__pill js-refresh" aria-label="刷新间隔">
          ${[1, 2, 3, 4, 5].map((minutes) => `<option value="${minutes * 60}" ${minutes === 2 ? "selected" : ""}>${minutes} min 刷新</option>`).join("")}
        </select>
        <button type="button" class="meter__pill js-theme">浅色</button>
        <button type="button" class="meter__pill js-retry">重试</button>
      </div>
    </div>
    <div class="meter__colhead"><span>跌幅阈值</span><span>家数</span></div>
    <div class="meter__chart">
      <img class="meter__poop" src="assets/poop_rgba.png" alt="">
      <svg class="meter__axis" viewBox="0 0 800 430" fill="none" aria-hidden="true">
        <line x1="400" y1="38" x2="400" y2="392" stroke="var(--axis)" stroke-width="1" stroke-dasharray="2 8"/>
      </svg>
      <div class="js-rows"></div>
      <svg class="meter__front js-contours" viewBox="0 0 800 430" fill="none"></svg>
      <div class="js-readouts"></div>
      <div class="js-chips"></div>
    </div>
    <div class="meter__baseline js-baseline" role="status" hidden></div>
    <div class="meter__settingsError js-settingsError" role="status" hidden></div>
    <div class="meter__alert js-alert" role="alert"></div>
    <div class="meter__veil" role="status">
      <div class="meter__spin" aria-hidden="true"></div>
      <div class="meter__veilText">数据获取中</div>
    </div>
  `;

  const meta = root.querySelector(".js-meta");
  const clock = root.querySelector(".js-clock");
  const refreshPill = root.querySelector(".js-refresh");
  const settingsError = root.querySelector(".js-settingsError");
  const themeBtn = root.querySelector(".js-theme");
  const retryBtn = root.querySelector(".js-retry");
  const baselineEl = root.querySelector(".js-baseline");
  const veilText = root.querySelector(".meter__veilText");
  const rowsEl = root.querySelector(".js-rows");
  const readoutsEl = root.querySelector(".js-readouts");
  const contourSvg = root.querySelector(".js-contours");
  const chipsEl = root.querySelector(".js-chips");
  const chartEl = root.querySelector(".meter__chart");
  const alertEl = root.querySelector(".js-alert");
  const ns = "http://www.w3.org/2000/svg";

  function settingError(key, message = "") {
    /* 输入：设置类别和可空错误文案；输出：无；用途：独立维护并显示错误；边界：单项成功不清除其它错误。 */
    if (message) settingErrors.set(key, message);
    else settingErrors.delete(key);
    settingsError.textContent = [...settingErrors.values()].join("；");
    settingsError.hidden = settingErrors.size === 0;
  }

  function setRefresh(seconds) {
    /* 输入：后端确认的正整数秒数；输出：无。
       用途：同步下拉选择；边界：非预设间隔显示当前配置值，无效值忽略。 */
    const value = Number(seconds);
    if (!Number.isInteger(value) || value <= 0) return;
    const label = value % 60 === 0 ? `${value / 60} min` : `${value} 秒`;
    refreshPill.querySelectorAll("option[data-custom]").forEach((option) => option.remove());
    if (![60, 120, 180, 240, 300].includes(value)) {
      const option = new Option(`${label} 刷新`, String(value));
      option.dataset.custom = "true";
      refreshPill.add(option);
    }
    refreshPill.value = String(value);
  }

  async function changeRefresh() {
    /* 输入：无，读取下拉值；输出：保存完成后的 Promise。
       用途：调用Python桥调整实际轮询并持久化；边界：失败回退到已确认值，保存时禁止重复提交。 */
    if (savingRefresh) return;
    const requested = Number(refreshPill.value);
    const token = ++refreshRequest;
    savingRefresh = true;
    refreshPill.disabled = true;
    settingError("refresh");
    try {
      if (!window.pywebview || !window.pywebview.api) throw new Error("桌面连接尚未就绪");
      const saved = await window.pywebview.api.set_refresh_seconds(requested);
      if (token !== refreshRequest) return;
      if (!Number.isInteger(saved) || saved <= 0) throw new Error("保存结果无效");
      refreshSeconds = saved;
      setRefresh(refreshSeconds);
    } catch (error) {
      if (token !== refreshRequest) return;
      setRefresh(refreshSeconds);
      settingError("refresh", `刷新间隔未保存：${error.message || error}`);
    } finally {
      if (token === refreshRequest) {
        savingRefresh = false;
        refreshPill.disabled = false;
      }
    }
  }
  refreshPill.addEventListener("change", changeRefresh);

  const rowRefs = THRESHOLDS.map((th, i) => {
    const row = document.createElement("div");
    row.className = "row";
    row.style.top = (ROW_TOP0 + i * ROW_H) + "px";
    const key = String(th).padStart(2, "0");
    row.innerHTML = `
      <div class="row__base"></div>
      <div class="row__fill" style="--c:var(--d${key})"></div>
    `;
    rowsEl.appendChild(row);
    const readout = document.createElement("div");
    readout.className = "readout";
    readout.style.top = row.style.top;
    readout.innerHTML = `
      <div class="row__label">&lt; -${th}%</div>
      <div class="row__value" style="--v:var(--v${key})">0</div>
    `;
    readoutsEl.appendChild(readout);
    return {
      th,
      base: row.querySelector(".row__base"),
      fill: row.querySelector(".row__fill"),
      value: readout.querySelector(".row__value"),
      live: 0,
    };
  });

  function setTheme(theme) {
    /* 输入：主题名；输出：无；用途：绘制主题；边界：非light值按dark显示，不隐式保存。 */
    const next = theme === "light" ? "light" : "dark";
    root.dataset.theme = next;
    document.body.dataset.theme = next;
    themeBtn.textContent = next === "dark" ? "浅色" : "深色";
  }

  async function changeTheme() {
    /* 输入：无；输出：保存完成的Promise；用途：切换主题并确认保存；边界：禁重入，失败恢复后端最新确认主题。 */
    if (savingTheme) return;
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    const token = ++themeRequest;
    savingTheme = true;
    themeBtn.disabled = true;
    settingError("theme");
    setTheme(next);
    try {
      if (!window.pywebview || !window.pywebview.api) throw new Error("桌面连接尚未就绪");
      const saved = await window.pywebview.api.set_theme(next);
      if (token !== themeRequest) return;
      if (!["dark", "light"].includes(saved)) throw new Error("保存结果无效");
      confirmedTheme = saved;
      setTheme(confirmedTheme);
    } catch (error) {
      if (token !== themeRequest) return;
      setTheme(confirmedTheme);
      settingError("theme", `主题未保存：${error.message || error}`);
    } finally {
      if (token === themeRequest) {
        savingTheme = false;
        themeBtn.disabled = false;
      }
    }
  }
  themeBtn.addEventListener("click", changeTheme);

  function paintRetry() {
    /* 输入：无；输出：无；用途：统一重试按钮显隐及忙碌状态；边界：正在取数时禁止再次触发。 */
    retryBtn.hidden = !retryVisible;
    retryBtn.disabled = retryPending || refreshing;
    retryBtn.textContent = refreshing ? "获取中" : "重试";
  }

  async function retryQuote() {
    /* 输入：无；输出：请求完成的Promise；用途：手动唤醒行情轮询；边界：失败保留重试能力，不伪造行情状态。 */
    if (retryPending || refreshing) return;
    retryPending = true;
    paintRetry();
    settingError("retry");
    try {
      if (!window.pywebview || !window.pywebview.api) throw new Error("桌面连接尚未就绪");
      const accepted = await window.pywebview.api.refresh_now();
      if (typeof accepted !== "boolean") throw new Error("重试结果无效");
    } catch (error) {
      settingError("retry", `重试未启动：${error.message || error}`);
    } finally {
      retryPending = false;
      paintRetry();
    }
  }
  retryBtn.addEventListener("click", retryQuote);

  function paintBar(el, count, scaleInset) {
    const half = halfPx(count, baseShallow, halfWidth, scaleInset);
    const width = half * 2;
    el.style.left = (CX - half) + "px";
    el.style.width = width + "px";
    el.style.display = width > 0 ? "block" : "none";
  }

  function paintLive(widths, texts) {
    rowRefs.forEach((row, i) => {
      row.fill.style.left = (CX - widths[i] / 2) + "px";
      row.fill.style.width = widths[i] + "px";
      row.fill.style.display = widths[i] > 0 ? "block" : "none";
      row.value.textContent = texts[i];
    });
  }

  function animateLive(nextCounts) {
    /* 输入：11档目标家数；输出：无；用途：平滑更新柱和数字；边界：新状态使令牌失效，不再允许旧动画覆写。 */
    const token = ++anim;
    const fromW = rowRefs.map((row) => parseFloat(row.fill.style.width) || 0);
    const toW = nextCounts.map((count) => halfPx(count, baseShallow, halfWidth, 1) * 2);
    const fromV = rowRefs.map((row) => Number(row.value.textContent.replace(/,/g, "")) || 0);
    const t0 = performance.now();
    function frame(now) {
      if (token !== anim) return;
      const ms = now - t0;
      const widths = [];
      const texts = [];
      rowRefs.forEach((row, i) => {
        widths.push(jumpWidth(fromW[i], toW[i], ms));
        const value = fromV[i] + (nextCounts[i] - fromV[i]) * Math.min(1, ms / JUMP_MS);
        texts.push(Math.round(value).toLocaleString("en-US"));
      });
      paintLive(widths, texts);
      if (ms < JUMP_MS) requestAnimationFrame(frame);
      else {
        nextCounts.forEach((count, i) => { rowRefs[i].live = count; });
        paintLive(toW, nextCounts.map((count) => count.toLocaleString("en-US")));
      }
    }
    requestAnimationFrame(frame);
  }

  function layoutCrashes(items) {
    /* 输入：历史轮廓列表；输出：按浅档峰值降序的列表。
       用途：安排层叠和标签顺序；边界：盖坐标不能跨越首末档中心。 */
    const ranked = items.map((item, index) => ({item, index}));
    ranked.sort((a, b) => (b.item.peak - a.item.peak) || (a.index - b.index));
    ranked.forEach((entry, rank) => {
      entry.item.ytop = Math.min(ROWS_Y[0] - 1, 30 + rank * 2);
      entry.item.ybot = Math.max(ROWS_Y[10] + 1, 400 - rank * 2);
      entry.item.rank = rank;
    });
    return ranked.map((entry) => entry.item);
  }

  function chipPosition(item) {
    const row = Math.min(10, item.rank + 1);
    const y = ROWS_Y[row] - 8;
    const half = item.halves[row] || 0;
    const guess = item.daily ? 78 : 52;
    let x = CX - half + 8;
    if (half < guess + 16) x = CX + 10;
    x = Math.max(8, Math.min(800 - guess - 4, x));
    return [round(x), round(y)];
  }

  function drawCrashes(items) {
    contourSvg.replaceChildren();
    chipsEl.replaceChildren();
    delete chartEl.dataset.hover;
    const ordered = layoutCrashes(items);
    // 大圈先画，小圈盖在上面，悬停才能点到内圈。
    ordered.forEach((item) => {
      const d = buildContour(item.halves, item.ytop, item.ybot);
      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      path.setAttribute("class", "contour");
      path.dataset.key = item.key;
      path.setAttribute("stroke-width", item.rank === 0 ? "2" : "1.5");
      path.setAttribute("stroke-dasharray", item.daily ? "10 8" : "4 3");
      const hit = document.createElementNS(ns, "path");
      hit.setAttribute("d", d);
      hit.setAttribute("class", "contour--hit");
      hit.dataset.key = item.key;
      contourSvg.append(path, hit);
      const chip = document.createElement("div");
      chip.className = "chip";
      chip.dataset.key = item.key;
      chip.textContent = item.label;
      const pos = chipPosition(item);
      chip.dataset.x = String(pos[0]);
      chip.dataset.y = String(pos[1]);
      chip.style.left = pos[0] + "px";
      chip.style.top = pos[1] + "px";
      chipsEl.appendChild(chip);
    });
    root.querySelectorAll(".contour--hit, .chip").forEach((el) => {
      el.addEventListener("mouseenter", () => setHot(el.dataset.key));
      el.addEventListener("mouseleave", () => setHot(""));
    });
  }

  function setHot(key) {
    if (key) chartEl.dataset.hover = key;
    else delete chartEl.dataset.hover;
    root.querySelectorAll(".contour").forEach((el) => {
      el.classList.toggle("is-hot", el.dataset.key === key);
    });
    root.querySelectorAll(".chip").forEach((el) => {
      const on = el.dataset.key === key;
      el.classList.toggle("is-hot", on);
      const item = crashes.find((crash) => crash.key === el.dataset.key);
      if (!item) return;
      el.textContent = on
        ? `${item.label}　>6% 最大 ${item.peak.toLocaleString("en-US")} 只`
        : item.label;
      placeChip(el, on);
    });
  }

  function placeChip(el, hot) {
    /* 输入：标签元素、是否处于悬停放大。
       输出：无。就地改 left/top。
       用途：放大后的长标签仍留在图内。
       边界：未悬停时回到最初落点。宽高尚未布局时不挪。 */
    const x0 = Number(el.dataset.x) || 0;
    const y0 = Number(el.dataset.y) || 0;
    if (!hot) {
      el.style.left = x0 + "px";
      el.style.top = y0 + "px";
      return;
    }
    const width = el.offsetWidth;
    const height = el.offsetHeight;
    if (width <= 0 || height <= 0) return;
    let x = x0;
    let y = y0 - Math.round((height - 17) / 2);
    if (x + width > 792) x = 792 - width;
    if (x < 8) x = 8;
    if (y + height > 422) y = 422 - height;
    if (y < 4) y = 4;
    el.style.left = round(x) + "px";
    el.style.top = round(y) + "px";
  }

  function apply(state) {
    /* 输入：后端页面状态；输出：无。
       用途：更新家数、轮廓和设置；边界：忽略重复或落后序号，保留保存中的新选择并取消旧动画。 */
    if (!state || typeof state !== "object") return;
    if (state && typeof state.seq === "number") {
      if (state.seq <= seen) return;
      seen = state.seq;
    }
    ++anim;
    settingError("bridge");
    confirmedTheme = state.theme === "light" ? "light" : "dark";
    if (!savingTheme) setTheme(confirmedTheme);
    if (Number.isInteger(state.refresh_seconds) && state.refresh_seconds > 0) {
      refreshSeconds = state.refresh_seconds;
      if (!savingRefresh) setRefresh(refreshSeconds);
    }
    halfWidth = Number(state.half_width) || 300;
    inset = Number(state.inset) || 0.9;
    const waiting = state.universe == null;
    refreshing = Boolean(state.refreshing);
    const failed = Boolean(state.stale) || Boolean(state.alert);
    const empty = state.universe === 0;
    root.classList.toggle("is-waiting", waiting);
    root.classList.toggle("is-loading", waiting && (!failed || refreshing));
    root.classList.toggle("is-empty", empty);
    root.classList.toggle("is-stale", failed);
    veilText.textContent = failed && !refreshing ? "行情中断，可在右上角重试" : "数据获取中";
    retryVisible = waiting || failed || empty;
    paintRetry();
    const alertText = String(state.alert || "");
    root.classList.toggle("is-alert", alertText.length > 0);
    alertEl.textContent = alertText;
    const bands = Array.isArray(state.bands) ? state.bands : [];
    const shallow = bands.find((band) => Number(band.th) === 6);
    const baselineReady = state.baseline_status === "ready" || (state.baseline_status == null && Number(shallow && shallow.base) > 0);
    const nextBase = baselineReady && shallow ? Number(shallow.base) || 0 : 0;
    baselineEl.hidden = baselineReady && nextBase > 0;
    baselineEl.textContent = String(state.baseline_message || "历史基底不可用，暂仅显示家数");
    baselineEl.title = baselineEl.textContent;
    const geo = [
      bands.map((band) => Number(band.th) + ":" + (Number(band.base) || 0)).join(","),
      baselineReady,
      halfWidth,
      inset,
      (state.crashes || []).map((crash) => (crash.id || "") + ":" + (crash.label || "") + ":" + (crash.source || "") + ":" + (crash.worst || []).join(",")).join("|"),
    ].join("#");
    const geometryChanged = geo !== geometrySig;
    if (geometryChanged) {
      geometrySig = geo;
      baseShallow = nextBase;
      const byBase = new Map(bands.map((band) => [Number(band.th), Number(band.base) || 0]));
      rowRefs.forEach((row) => paintBar(row.base, byBase.get(row.th) || 0, 1));
      const nextCrashes = (nextBase > 0 ? state.crashes || [] : []).map((crash, index) => {
        const worst = crash.worst || [];
        const peak = Number(worst[worst.length - 1]) || 0;
        return {
          key: String(crash.id || crash.label || index),
          label: crash.label || "",
          daily: crash.source === "1d",
          peak,
          halves: worst.map((count) => halfPx(Number(count) || 0, baseShallow, halfWidth, inset)),
        };
      }).filter((item) => item.peak > 0 || item.halves.some((half) => half > 0));
      crashes = nextCrashes;
      drawCrashes(crashes);
    }
    const byLive = new Map(bands.map((band) => [Number(band.th), band.live]));
    const nextLive = rowRefs.map((row) => {
      const live = byLive.get(row.th);
      return live != null ? Number(live) || 0 : row.live;
    });
    const liveChanged = nextLive.some((value, i) => value !== rowRefs[i].live);
    if (liveChanged && !state.computing) animateLive(nextLive);
    else {
      rowRefs.forEach((row, i) => { row.live = nextLive[i]; });
      paintLive(
        nextLive.map((count) => halfPx(count, baseShallow, halfWidth, 1) * 2),
        nextLive.map((count) => count.toLocaleString("en-US")),
      );
    }

    if (empty) {
      meta.textContent = "暂无有效成交";
    } else if (state.computing) {
      meta.textContent = "正在计算基底";
    } else if (state.message) {
      meta.textContent = state.message;
    } else if (state.universe != null) {
      meta.textContent = `有效股票 ${Number(state.universe).toLocaleString("en-US")} 只　·　累计口径，深跌计入浅档`;
    } else {
      meta.textContent = failed ? "行情中断" : "等待行情";
    }
    clock.textContent = state.clock || "--:--:--";
  }

  setTheme(root.dataset.theme || "dark");
  root.classList.add("is-waiting", "is-loading");
  return {apply, setTheme, showBridgeError(message) {
    /* 输入：桥接错误文案；输出：无；用途：初始取状态失败时显示恢复入口；边界：保留已有行情。 */
    settingError("bridge", message);
    root.classList.remove("is-loading");
    veilText.textContent = "桌面连接异常，可在右上角重试";
    retryVisible = true;
    paintRetry();
  }};
}

function fitMeter() {
  /* 输入：无，读取实际客户区与设计画布尺寸；输出：无。
     用途：等比缩放所有文字、图形和交互元素并保持居中；边界：最小化时零尺寸不更新。 */
  const root = document.getElementById("meter");
  const width = document.documentElement.clientWidth;
  const height = document.documentElement.clientHeight;
  if (width <= 0 || height <= 0) return;
  const scale = Math.min(width / root.offsetWidth, height / root.offsetHeight);
  root.style.setProperty("--meter-scale", String(scale));
}

window.meter = createMeter(document.getElementById("meter"));
new ResizeObserver(fitMeter).observe(document.documentElement);
window.addEventListener("resize", fitMeter);
fitMeter();

window.addEventListener("pywebviewready", () => {
  window.pywebview.api.get_state().then((state) => {
    if (state) window.meter.apply(state);
  }).catch((error) => window.meter.showBridgeError(`状态获取失败：${error.message || error}`));
});

function demoState(overflow) {
  const bands = [
    [16, 1751], [15, 1857], [14, 1929], [13, 1974], [12, 2024], [11, 2144],
    [10, 4152], [9, 4795], [8, 4901], [7, 4986], [6, 5043],
  ].map(([th, base]) => ({
    th,
    base,
    live: overflow ? Math.round(base * 1.18) : Math.round(base * 0.42),
  }));
  const pack = (id, label, source, worst) => ({id, label, source, worst});
  return {
    theme: "dark",
    computing: false,
    stale: false,
    clock: "10:14:02",
    universe: 5349,
    message: "",
    refresh_label: "2 min 刷新",
    refresh_seconds: 120,
    baseline_status: "ready",
    baseline_message: "",
    refreshing: false,
    half_width: 300,
    inset: 0.9,
    bands,
    crashes: [
      pack("20250401-0409", "25-04", "15m", [1751, 1857, 1929, 1974, 2024, 2144, 4152, 4795, 4901, 4986, 5043]),
      pack("20240122-0205", "24-02 日线", "1d", [325, 475, 641, 809, 986, 1226, 2379, 2980, 3247, 3486, 3721]),
      pack("20220421-0427", "22-04 日线", "1d", [32, 49, 76, 144, 258, 551, 1419, 2168, 2734, 3223, 3550]),
      pack("20220307-0315", "22-03 日线", "1d", [7, 10, 20, 31, 44, 94, 224, 370, 688, 1252, 2114]),
      pack("20260717-0730", "26-07", "15m", [55, 75, 106, 165, 239, 341, 529, 867, 1133, 1471, 1910]),
      pack("20250103-0113", "25-01", "15m", [12, 13, 16, 17, 22, 33, 121, 221, 347, 637, 1115]),
      pack("20240816-20240923", "24-08 日线", "1d", [7, 8, 12, 12, 16, 18, 28, 41, 59, 92, 155]),
    ],
  };
}

const params = new URLSearchParams(location.search);
if (params.get("demo") === "1" || params.get("demo") === "overflow") {
  window.meter.apply(demoState(params.get("demo") === "overflow"));
}
