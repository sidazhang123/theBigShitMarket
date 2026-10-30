/* 急跌梯度计量器。
   几何、闭环和过冲沿用施工包。家数、比例尺和主题由页面状态推进。
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
  if (baseShallow <= 0 || count <= 0 || halfWidth <= 0 || inset <= 0) return 0;
  return Math.round(count * inset * halfWidth / baseShallow);
}

function buildContour(halves, ytop, ybot, cx = CX, k = 0.8) {
  const p = [[cx - halves[0] * 0.55, ytop], [cx + halves[0] * 0.55, ytop]];
  halves.forEach((h, i) => p.push([cx + h, ROWS_Y[i]]));
  p.push([cx + halves[10] * 0.55, ybot], [cx - halves[10] * 0.55, ybot]);
  for (let i = 10; i >= 0; i--) p.push([cx - halves[i], ROWS_Y[i]]);
  const n = p.length;
  const f = (v) => v.toFixed(1);
  let d = `M${f(p[0][0])} ${f(p[0][1])}`;
  for (let i = 0; i < n; i++) {
    const p0 = p[(i - 1 + n) % n];
    const p1 = p[i];
    const p2 = p[(i + 1) % n];
    const p3 = p[(i + 2) % n];
    const c1x = p1[0] + (p2[0] - p0[0]) / 6 * k;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6 * k;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6 * k;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6 * k;
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
  const round = (n) => Math.round(n);
  let seen = -1;
  let anim = 0;
  let crashes = [];
  let baseShallow = 0;
  let halfWidth = 300;
  let inset = 0.9;
  let geometrySig = "";

  root.innerHTML = `
    <svg class="meter__hex" viewBox="0 0 880 640" fill="none" aria-hidden="true">
      <path d="${hexTexture(880, 640, 78)}" stroke="var(--txt-3)" stroke-opacity=".16" stroke-width="1"/>
    </svg>
    <div class="meter__header">
      <div>
        <div class="meter__overline">A-SHARE · CRASH DENSITY</div>
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
        <div class="meter__pill js-refresh">2 min 刷新</div>
        <button type="button" class="meter__pill js-theme">浅色</button>
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
    <div class="meter__footer">
      <span>基底 · quant.duckdb 15min · 单档历史最大值</span>
      <span>填充 · 标准行情 · 每 2 min 轮询</span>
    </div>
    <div class="meter__veil" role="status">
      <div class="meter__spin" aria-hidden="true"></div>
      <div class="meter__veilText">数据获取中</div>
    </div>
  `;

  const meta = root.querySelector(".js-meta");
  const clock = root.querySelector(".js-clock");
  const refreshPill = root.querySelector(".js-refresh");
  const themeBtn = root.querySelector(".js-theme");
  const rowsEl = root.querySelector(".js-rows");
  const readoutsEl = root.querySelector(".js-readouts");
  const contourSvg = root.querySelector(".js-contours");
  const chipsEl = root.querySelector(".js-chips");
  const chartEl = root.querySelector(".meter__chart");
  const ns = "http://www.w3.org/2000/svg";

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
    const next = theme === "light" ? "light" : "dark";
    root.dataset.theme = next;
    document.body.dataset.theme = next;
    themeBtn.textContent = next === "dark" ? "浅色" : "深色";
  }

  themeBtn.addEventListener("click", () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    setTheme(next);
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.set_theme(next);
    }
  });

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
    const token = ++anim;
    const fromW = rowRefs.map((row) => parseFloat(row.fill.style.width) || 0);
    const toW = nextCounts.map((count) => halfPx(count, baseShallow, halfWidth, 1) * 2);
    const fromV = rowRefs.map((row) => row.live);
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
    const ranked = items.map((item, index) => ({item, index}));
    ranked.sort((a, b) => (b.item.peak - a.item.peak) || (a.index - b.index));
    ranked.forEach((entry, rank) => {
      entry.item.ytop = Math.min(46, 30 + rank * 2);
      entry.item.ybot = Math.max(384, 400 - rank * 2);
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
    });
  }

  function apply(state) {
    if (state && typeof state.seq === "number") {
      if (state.seq < seen) return;
      seen = state.seq;
    }
    setTheme(state.theme || "dark");
    if (state.refresh_label) refreshPill.textContent = state.refresh_label;
    halfWidth = Number(state.half_width) || 300;
    inset = Number(state.inset) || 0.9;
    const waiting = state.universe == null;
    root.classList.toggle("is-waiting", waiting);
    root.classList.toggle("is-stale", Boolean(state.stale) && !waiting);
    const bands = Array.isArray(state.bands) ? state.bands : [];
    const shallow = bands.find((band) => Number(band.th) === 6);
    const nextBase = shallow ? Number(shallow.base) || 0 : 0;
    const geo = [
      bands.map((band) => Number(band.th) + ":" + (Number(band.base) || 0)).join(","),
      halfWidth,
      inset,
      (state.crashes || []).map((crash) => (crash.id || "") + ":" + (crash.label || "") + ":" + (crash.worst || []).join(",")).join("|"),
    ].join("#");
    const geometryChanged = geo !== geometrySig;
    if (geometryChanged) {
      geometrySig = geo;
      baseShallow = nextBase;
      const byBase = new Map(bands.map((band) => [Number(band.th), Number(band.base) || 0]));
      rowRefs.forEach((row) => paintBar(row.base, byBase.get(row.th) || 0, 1));
      const nextCrashes = (state.crashes || []).map((crash, index) => {
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

    if (state.computing) {
      meta.textContent = "正在计算基底";
    } else if (state.message) {
      meta.textContent = state.message;
    } else if (state.universe != null) {
      meta.textContent = `全市场 ${Number(state.universe).toLocaleString("en-US")} 只　·　累计口径，深跌计入浅档`;
    }
    clock.textContent = state.clock || "--:--:--";
  }

  setTheme(root.dataset.theme || "dark");
  root.classList.add("is-waiting");
  return {apply, setTheme};
}

window.meter = createMeter(document.getElementById("meter"));

window.addEventListener("pywebviewready", () => {
  window.pywebview.api.get_state().then((state) => {
    if (state) window.meter.apply(state);
  });
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
