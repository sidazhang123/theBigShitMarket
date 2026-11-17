# 狗屎市场（The Big Shit Market）

一个面向 A 股全市场的实时「急跌梯度」监控桌面应用：把沪深北全部股票按跌幅分成 11 档（跌超 16% 到跌超 6%），用灰色历史基底柱 + 虚线股灾包络 + 彩色实时柱，直观呈现当前市场的恐慌程度。

## 功能特性

- **11 档累计跌幅计数**：从上到下依次是跌超 16%、15%……跌超 6%。使用严格小于门槛的口径，恰好跌到阈值不计入该档；收益率先统一精度，避免浮点尾差造成误计。
- **实时行情轮询**：按 `config.yaml` 中 `quote_hosts` 的顺序逐台尝试标准行情服务器，同一台使用 4 条连接同时分页；单台失败自动切换下一台，全部失败保留上一帧家数并显示告警。
- **历史基底灰柱**：展示历史极端行情中各档曾经达到的最大家数，按家数比例、沿中轴对齐；数字预先算好写入 `cache/baselines.json`。
- **股灾虚线包络**：针对 `crash_windows` 配置的每一段股灾日期，取各档最大家数并乘 0.9 连成平滑虚线，日期牌只标到峰值所在月份。
- **彩色实时柱**：当前各档家数嵌入灰柱，从浅跌的绿渐变到深跌的红，实时数可长过灰柱。
- **数据过滤**：实时计数剔除成交量为 0 或负数、现价/昨收不为有限正数以及价量字段无法解析的股票；顶部的「有效股票」即过滤后参与统计的股票数。
- **可交互界面**：右上角可切换明暗主题、下拉选择 1–5 分钟刷新间隔（选择后保存到配置）、失败时提供「重试」按钮。
- **历史缓存校验**：启动只读取缓存、不连接历史数据库；缓存缺失、失效、损坏或与当前算法/日期段不匹配时，界面独立提示原因。

## 技术栈 / 依赖

- **语言**：Python 3.10+（源码使用 `X | None` 联合类型与 `from __future__ import annotations`）
- **桌面窗口**：`pywebview`（Windows 下使用 EdgeChromium/WebView2 后端，需要 Edge WebView2 Runtime）
- **科学计算**：`numpy`（实时涨幅筛选与分档计数）
- **配置**：`PyYAML`（`config.yaml` 的读取与回写）
- **历史数据**：`duckdb`（仅在离线重算历史基底时使用，打包时被排除）
- **打包**：`PyInstaller`（仅打包时需要）
- **测试**：`pytest`

> 仓库未提供 `requirements.txt` / `pyproject.toml`，依赖从 `app/` 模块的导入推断。核心运行依赖为 `pywebview`、`numpy`、`PyYAML`；`duckdb` 只在重算历史缓存时需要。

## 目录结构

```text
theBigShitMarket/
├── app/                    # 后端核心模块
│   ├── __main__.py         # python -m app 入口
│   ├── main.py             # 桌面窗口入口、pywebview 桥、日志与退出逻辑
│   ├── config.py           # config.yaml 读取与回写（含校验）
│   ├── count.py            # 11 档累计跌幅计数（实时与测试共用）
│   ├── live.py             # 实时行情轮询与故障切换
│   ├── quote_client.py     # 标准行情协议客户端（连接、分页、解析）
│   ├── history.py          # 历史基底与股灾轮廓重算（DuckDB）
│   ├── baseline_cache.py   # 历史缓存读取与校验
│   ├── scale.py            # 家数到像素的比例尺
│   ├── storage.py          # 本地文本原子写入与跨进程锁
│   ├── paths.py            # 源码运行 / 打包运行路径解析
│   └── window_layout.py    # 窗口首次显示时的工作区居中
├── web/                    # 前端界面（HTML/CSS/JS + 图标资源）
│   ├── index.html
│   ├── meter.js            # 图表绘制、动画与交互
│   ├── meter.css / tokens.css
│   └── assets/             # app.ico、app-icon.png、poop_rgba.png 等
├── cache/baselines.json    # 预生成的历史基底缓存
├── tests/                  # pytest 测试
├── config.yaml             # 可调参数配置
├── theBigShitMarket.spec   # PyInstaller 打包配置
└── README.md
```

## 安装与环境要求

- **操作系统**：Windows（窗口居中、启动错误对话框、进程退出均使用 Win32 API；非 Windows 平台会退化为框架默认行为）
- **Python**：3.10 及以上
- **Edge WebView2 Runtime**：pywebview 的 EdgeChromium 后端所需
- **依赖安装**（示例）：

```text
pip install pywebview numpy PyYAML
pip install duckdb        # 仅重算历史基底时需要
pip install PyInstaller   # 仅打包时需要
```

## 使用方法

### 启动应用

在项目根目录执行：

```text
py -m app.main
```

等价入口：`python -m app`（对应 `app/__main__.py`）。

启动流程：读取 `config.yaml` → 加载 `cache/baselines.json` 作为历史基底 → 打开客户区为 816×530 的可缩放窗口 → 后台线程按配置间隔轮询实时行情。

### 重算历史缓存

修改 `config.yaml` 中的 `crash_windows`（或 `min_codes`、`formula_version`）后，在可读取源行情库的本机重新生成历史缓存：

```text
py -c "from app.config import load_config; from app.history import recompute_baselines; recompute_baselines(load_config())"
```

该命令只读打开 `source_db` 指定的 DuckDB 库，重算灰色基底与股灾轮廓，并原子替换写入 `cache/baselines.json`。

### 打包为 exe

在项目根目录执行：

```text
py -m PyInstaller --noconfirm theBigShitMarket.spec
```

spec 使用单文件、无控制台模式（对应 `-F -w`），包含 `web` 全部静态资源、默认 `config.yaml` 和最新 `cache/baselines.json`。不要只用 `-F -w app/__main__.py`，那样不会自动收集上述资源。输出为 `dist/theBigShitMarket.exe`。

### 运行测试

```text
py -m pytest -q
```

前端验收使用本机 Edge 的无界面模式；真实历史库存在时会额外执行只读核验，测试缓存写到临时目录。

## 配置说明

全部可调参数位于项目根目录的 `config.yaml`（打包后位于 exe 旁）。关键配置项：

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `source_db` | — | 历史行情库路径（DuckDB）。只在 `recompute_baselines` 时读取，启动不检查、不连接 |
| `refresh_seconds` | `120` | 行情刷新间隔（秒）。界面可选 60 / 120 / 180 / 240 / 300 |
| `min_codes` | `4500` | 单根 15 分钟 K 线参与统计的最少有效股票数（历史重算口径） |
| `half_width_px` | `300` | 跌超 6% 档灰柱的半宽（像素） |
| `contour_inset` | `0.9` | 虚线包络的内缩系数，取值范围 `(0, 1]` |
| `formula_version` | `1` | 公式版本，参与缓存指纹 |
| `theme` | `dark` | 界面主题，取值 `dark` 或 `light` |
| `quote_hosts` | — | 行情服务器列表，每项含 `host`（必填）与 `port`（缺省 7709）。列表顺序即故障切换优先级 |
| `quote_timeout` | `8` | 连接及整帧超时秒数，分片接收不重置帧时限 |
| `quote_deadline_seconds` | `30` | 单台服务器全部握手和分页的总时限 |
| `quote_min_stocks` | `4500` | 原始唯一股票最少数（完整性校验） |
| `quote_min_coverage` | `0.9` | 相对上次完整快照原始股票数的最低比例 |
| `crash_windows` | — | 股灾日期段列表。格式 `YYYYMMDD-MMDD` 或 `YYYYMMDD-YYYYMMDD`，不允许重复日期范围 |

行情地址配置示例（顺序即优先级）：

```yaml
quote_hosts:
  - host: "114.117.72.207"
    port: 7709
  - host: "123.125.108.101"
    port: 7709
quote_timeout: 8
quote_deadline_seconds: 30
quote_min_stocks: 4500
quote_min_coverage: 0.9
```

`quote_min_stocks` 与 `quote_min_coverage` 按过滤前的唯一股票数校验，二者取较大值检查，不使用价量过滤后的有效数。手改地址与保护参数后需重启程序生效。

## 输出 / 结果说明

- **图表界面**：816×530 的紧凑画布，按客户区等比例缩放并居中；11 档从上到下为跌超 16% → 6%，每档显示阈值与当前家数。
- **灰色柱**：历史基底，长度按家数比例、沿中轴对齐；数据来自 `cache/baselines.json` 的 `bands[].base`。
- **虚线**：历次股灾包络，数据来自 `crashes[]`；每段含 `id`（日期段）、`label`（峰值月份，如 `25-04`）、`source`（`15m` 分钟线或 `1d` 日线）、`worst`（11 档家数）。
- **彩色柱**：当前实时家数，嵌在灰柱里；悬停某条虚线时，读的是该段跌超 6% 曾经达到的最大家数。
- **日志**：运行故障记录在程序旁的 `logs/meter.log`，滚动保留约 3 MB。
- **打包产物**：`dist/theBigShitMarket.exe`，单文件、无控制台。

## 注意事项 / 已知限制

- 实时行情依赖配置的标准行情服务器地址，默认地址按 `quote_hosts` 顺序逐台尝试；全部地址失败时保留上一帧家数并显示告警，下轮重新从第一台开始。
- 完整回包但全部股票无有效成交时，正常显示「暂无有效成交」，不误报网络失败。
- 历史基底为零（`zero_base`）时，界面暂只显示家数，不按历史比例绘图。
- 缺少某交易日的合格 15 分钟数据时，该段改用日线收盘计算（日期牌显示「日线」），且不参与灰色柱取最大。
- 配置、升级和缓存均先写同目录临时文件再原子替换；配置读改写另有跨线程/进程锁，多窗口保存不会丢失彼此的设置。
- 启动配置错误会弹出错误对话框（Windows）或输出到标准错误（其它平台）。
- 打包后的 exe 启动时从包内读取网页和历史缓存，无需源数据库；历史日期段与包内缓存对齐，重新打包前需先完成历史重算。
