# 📊 多因子监控面板（Multi-Factor Monitor Panel）

一个**原生 Windows 桌面窗口**（PySide6，非浏览器）的行情与因子看板：A 股 / 美股 / 加密货币三市场横截面打分排名，真实期权链与 Black-Scholes 对照，全球财经快讯聚合，DeepSeek AI 解读，并可作为 **MCP 服务端**接入 DeepSeek Harness 等 Agent 框架。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/PySide6-6.5%2B-41CD52?logo=qt&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

> 本项目仅为工具与教学演示，所有数据、因子得分与理论定价均不构成任何投资建议。

---

## ✨ 功能一览

| 模块 | 能力 |
| --- | --- |
| **因子监控** | 估值 / 动量 / 质量 / 波动 / 流动性五因子横截面百分位打分，加权综合分与评级，支持筛选、排序、CSV 导出 |
| **多市场** | A 股（东财 → 新浪 → 代码表三级降级）、美股（腾讯报价 + 东财 PB + 新浪历史）、加密货币（Binance 公开数据） |
| **外币折人民币** | 美股 / 加密市场价格旁自动给出「折人民币(¥)」列，汇率取新浪在岸即期，表头标注原币种 |
| **个股折线图** | 双击任意股票弹出独立窗口：当日 5 / 15 分钟分时、近 120 日日线，60 秒自动刷新，支持跟随主表选中切换 |
| **期权观察** | 中金所股指期权（IO / HO / MO）与上交所 ETF 期权（510050/510300/510500/588000）**真实期权链**：隐含波动率、Delta、持仓量、平价偏离，并可切换 BS 理论对照 |
| **全球新闻** | 东方财富 + 财联社快讯，可扩展 RSS；后台线程加载，设置项（来源 / 条数 / 自动刷新间隔）自动持久化 |
| **AI 助手** | 填入 DeepSeek API Key，自动带当前打分表与新闻作为上下文解读、追问 |
| **MCP 服务端** | `mcp_server.py` 暴露打分表 / 个股明细 / 新闻 / 自选 / 期权链工具，供外部 Agent 调用 |

---

## 📸 界面预览

| 因子监控（真实行情 + 五因子得分条形） | 真实期权链（IO / ETF，含 IV 与 Delta） |
| --- | --- |
| ![因子监控](docs/screenshots/main.png) | ![期权观察](docs/screenshots/options.png) |

![个股折线图](docs/screenshots/chart.png)

---

## 🚀 快速开始（Windows）

### 方式 A：双击启动（推荐，不需要打包）

1. 安装 **Python 3.10+**（勾选 *Add Python to PATH*）：<https://www.python.org/downloads/>
2. 双击 **`run_windows.bat`**
   - 首次运行自动安装依赖（需联网）；
   - 之后直接弹出桌面窗口，**不会闪出 CMD 黑框**（`launcher.pyw` + `pythonw.exe` 启动）；
   - 启动日志写到 `logs/launcher.log`。

### 方式 B：打包成独立 exe

双击 **`build_windows.bat`**，自动安装 PyInstaller 并打包，产物在 `dist\MultiFactorPanel.exe`，可单独复制到其他 Windows 电脑运行。

> 打包不能跨平台：Linux / macOS 上无法直接产出 Windows exe。

### 方式 C：命令行

```bat
python -m pip install -r requirements.txt
python desktop_app.py
```

---

## 🗂 项目结构

```
multi_factor_panel/
├── desktop_app.py            # ★ 桌面程序主入口（PySide6 窗口、因子表、期权页、新闻页、AI 页、折线图窗口）
├── mcp_server.py             # ★ MCP 服务端（stdio），把看板数据暴露给外部 Agent
├── launcher.pyw              # 无控制台启动器（pythonw 启动，异常写日志并弹窗）
├── run_windows.bat           # 双击启动
├── build_windows.bat         # 一键打包 exe
├── requirements.txt          # 完整依赖
├── requirements-desktop.txt  # 桌面版最小依赖（bat 脚本使用）
├── MultiFactorPanel.spec     # PyInstaller 配置
├── config/
│   └── watchlist.yaml        # 股票池、权重、新闻源、DeepSeek、期权品种等全部配置
├── data/
│   ├── universe_a.csv        # A 股全市场代码表（5000+ 只真实代码与名称）
│   └── universe_us.csv       # 美股代码表（65 只，含中概）
├── dsh/
│   └── monitor-panel.cordis.yml  # dsh MCP client 配置示例
└── src/
    ├── data_source.py        # 三市场行情接入与降级策略
    ├── factors.py            # 因子计算与横截面打分
    ├── screener.py           # 条件筛选与 CSV 导出
    ├── options.py            # 真实期权链、IV 反解、BS 定价与希腊字母
    ├── crypto.py             # 加密货币行情与人民币汇率
    ├── chartdata.py          # 个股分钟线 / 日线数据
    ├── news.py               # 新闻聚合
    ├── ai_assistant.py       # DeepSeek 对话接入
    ├── uistate.py            # 界面设置持久化
    └── paths.py              # 资源路径解析（兼容 PyInstaller）
```

## 🔄 数据流

```mermaid
flowchart LR
    A[config/watchlist.yaml] --> B[src/data_source.py]
    B -->|A 股| C1[东方财富<br/>含 PE/PB/换手]
    B -->|A 股降级| C2[新浪<br/>仅价格]
    B -->|美股| C3[腾讯报价 + 东财 PB + 新浪历史]
    B -->|加密| C4[Binance data-api]
    B -->|全部失败| C5[data/universe_*.csv<br/>仅代码与名称]
    C1 & C2 & C3 & C4 & C5 --> D[src/factors.py<br/>五因子打分]
    D --> E[desktop_app.py<br/>因子表 / 明细 / 折线图]
    D --> F[mcp_server.py<br/>MCP 工具]
    G[src/options.py] --> E
    H[src/news.py] --> E
```

---

## ⚙️ 配置：`config/watchlist.yaml`

```yaml
stocks:                       # A 股自选（6 位代码）
  - {code: "600519", name: "贵州茅台"}
us_stocks:                    # 美股自选
  - {code: "AAPL", name: "苹果"}
crypto: ["BTC"]               # 加密货币（Binance 交易对前缀）
options_a: {symbol: 华泰柏瑞沪深300ETF期权, end_month: ""}
options_us: {symbol: AAPL}

news:
  limit: 50
  sources: ["eastmoney", "cls"]
  auto_refresh_minutes: 0     # 0 = 仅手动刷新
  rss_feeds: []               # 可选境外 RSS：[{name, url}]

deepseek:
  api_key: ""                 # 留空则在界面里临时输入
  base_url: https://api.deepseek.com
  model: deepseek-flash

weights:                      # 权重无需相加为 1，程序自动归一化
  valuation: 0.25
  momentum: 0.20
  quality: 0.25
  volatility: 0.15
  liquidity: 0.15

settings:
  momentum_window: 20
  top_n: 10
  data_source: auto           # auto / live / codes
  pool_size: 57
```

### 因子口径

| 因子 | 含义 | 方向 |
| --- | --- | --- |
| valuation 估值 | PE、PB 越低越便宜 | 越低越好 |
| momentum 动量 | 近 20 日涨跌幅 | 越高越好 |
| quality 质量 | ROE 净资产收益率 | 越高越好 |
| volatility 波动 | 近 20 日收益率标准差 | 越低越稳健 |
| liquidity 流动性 | 成交额活跃度 | 越高越好 |

评级：`≥80 强势` / `60~80 偏强` / `40~60 中性` / `<40 偏弱`。缺失指标按中性 50 分处理，界面显示 `—`。

---

## 🌐 数据来源

| 数据 | 主源 | 降级 / 备注 |
| --- | --- | --- |
| A 股快照 | 东方财富 `stock_zh_a_spot_em`（价格、PE、PB、换手、量比） | 新浪 `stock_zh_a_spot_sina`（仅价格）；再降级为代码表 |
| A 股日线 | `stock_zh_a_hist_em` | akshare ≥ 1.20.46 已改名，旧名会抛 `AttributeError` |
| 美股报价 | 腾讯 `qt.gtimg.cn`（价格、涨跌幅、成交额、PE） | 代码需把 `BRK-B` 写成 `BRK.B` |
| 美股 PB | 东方财富 ulist 批量（`f23` 才是市净率×100） | 逐只 `push2` 兜底 |
| 美股动量 / 波动 | 新浪 `US_MinKService.getDailyK` 全历史日线 | yfinance（常 429） |
| 加密货币 | `data-api.binance.vision`（唯一可直连的 Binance 域） | 其余交易所域名在部分网络下不可达 |
| 人民币汇率 | 新浪 `fx_susdcny`（在岸即期，GBK + Referer） | — |
| 期权链 | 新浪：中金所 `option_cffex_*`、上交所 ETF `option_sse_*` | 东财期权接口常限流 |
| 个股 K 线 | 新浪 `CN_MarketDataService.getKLineData` | 美股分时无公开可用源，只提供日线 |
| 新闻 | 东方财富、财联社 | RSS 可扩展 |

### 期权定价的两个关键点

1. **股指期权的标的是股指期货**，不是沪深 300 现货。程序先用看跌-看涨平价 `F = K + (C − P)·e^{rT}` 反解隐含远期，再以 `S = F`、`q = r` 反解隐含波动率；否则会得到「Call 13% vs Put 25.7%」的假微笑。
2. ETF 期权到期日为**第四个周三**，中金所股指期权为**第三个周五**。

---

## 🤖 接入 DeepSeek Harness（dsh）

`mcp_server.py` 以 stdio 方式暴露只读工具，模型侧工具名为 `mcp__monitor__<name>`：

| 工具 | 作用 |
| --- | --- |
| `list_stock_scores` | 多因子打分排名表（可选 `market`：A股 / 美股 / 加密货币） |
| `get_stock_detail` | 单只个股明细（`code` + `market`） |
| `list_latest_news` | 最新财经快讯 |
| `list_watchlist` | 自选清单 |
| `list_option_markets` | 可用期权品种与到期月 |
| `list_option_chain` | 真实期权链（`market`、`expiry`、`max_rows`） |

接入步骤：

1. `python -m pip install -r requirements.txt`（需 `mcp<2.0`，2.x 已把 `FastMCP` 改名，代码不兼容）
2. 编辑 `dsh/monitor-panel.cordis.yml`，把 `args` 换成你本机 `mcp_server.py` 的绝对路径，`command` 填 `python`（找不到就填 `py`）
3. 临时生效：`dsh web --patch "<该文件的绝对路径>"`；长期生效：把 `- insert:` 整段合并进 `$DSH_HOME/profiles/<profile>/cordis.patch.yml`
4. 在 dsh 会话里提问，例如「看看打分最高的几只」「查一下 600519 的因子明细」

---

## ❓ 常见问题

**Q：切到「实时行情」后价格有，但 PE/PB/ROE/动量是「—」？**
A：主源东方财富被临时限流时会自动切新浪（只含价格），估值类因子留空。等东财恢复后点「刷新」自动补齐。

**Q：美股价格显示为「—」？**
A：确认代码拼写（`BRK-B` → `BRK.B`）。腾讯批量报价失败时会回落代码表，此时只有代码与名称。

**Q：ROE 为什么经常为空？**
A：免费快照不含 ROE，质量因子按中性 50 分处理。可在 `src/data_source.py` 补充财务报表接口。

**Q：提示「未检测到 Python」？**
A：安装 Python 3.10+ 并勾选 *Add Python to PATH*，或改用 `py -3`。

**Q：依赖装得很慢？**
```bat
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

**Q：DeepSeek Key 会被上传吗？**
A：不会。Key 只存在本地（界面输入或 `watchlist.yaml`），请勿把填了 Key 的配置提交到仓库。

**Q：想新增因子？**
A：在 `src/factors.py` 的 `FACTOR_DIRECTION` / `FACTOR_LABELS` 注册，在 `_build_raw_factors()` 给原始值，再到 `watchlist.yaml` 加权重。

---

## 🧱 已知限制

- 仅针对 Windows 做了验证（`run_windows.bat` / `.pyw` 启动器 / PyInstaller 配置）；Linux、macOS 需自行改启动方式。
- 免费行情接口随时可能限流或改名（akshare 尤其频繁），代码里已做多级降级，但仍可能出现短时缺数。
- 美股分时线无可用公开数据源，只有日线。
- 期权链来自新浪公开接口，深度虚值合约 IV 可能为空（价格过小，反解不收敛）。
- 不包含任何交易、下单功能。

---

## 🤝 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。欢迎提 Issue、补充数据源适配、改进界面。

## 📄 许可证

[MIT](LICENSE) © 2026 YG

## ⚠️ 免责声明

本项目仅供学习与技术研究。行情数据来自第三方公开接口，可能存在延迟、缺失或错误；因子得分与期权理论价均为数学计算结果，**不构成任何投资建议**。据此操作风险自负。
