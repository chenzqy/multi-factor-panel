# 架构与数据源细节

这份文档记录实现层面的取舍，主要是踩过的坑。改代码前建议先读一遍。

## 分层

```
UI (desktop_app.py, PySide6)
   │  只做展示与交互，网络请求全部走 src/
   ▼
src/data_source.py   行情与横截面数据（A股 / 美股 / 加密）
src/options.py       期权链 + 波动率反解
src/chartdata.py     个股 K 线
src/news.py          财经快讯
src/crypto.py        币价与人民币汇率
   │
   ▼
src/factors.py       纯计算：五因子 → 百分位 → 加权综合分
```

约定：

- 各数据模块对外返回 **列名统一** 的 DataFrame，缺失值用 `NaN`，由 UI 负责显示为 `—`
- 数据来源写在 `df.attrs["source"]`（`live` / `codes` / `crypto` …），状态栏与概览条据此提示
- 网络请求默认带 20 秒级缓存，避免切页 / 刷新反复打到同一接口

## 行情降级策略

`get_universe(market, source, ...)` 的 `source` 有三种取值：

| source | 行为 |
| --- | --- |
| `auto` | 先尝试实时接口，全失败则回落本地代码表（`attrs["source"]="codes"`） |
| `live` | 只用实时接口，失败即抛错 |
| `codes` | 只读 `data/universe_*.csv`，不发任何网络请求 |

### A 股

1. 东方财富 `stock_zh_a_spot_em`：一次返回价格、涨跌幅、PE、PB、换手、量比、成交额
2. 被限流（`Push2RateLimitError`）→ 新浪 `stock_zh_a_spot_sina`：只有价格与涨跌幅，估值列留 `NaN`
3. 再失败 → 本地代码表

> akshare 1.20.46 起 `stock_zh_a_hist` 改名 `stock_zh_a_hist_em`，旧名直接抛 `AttributeError`，代码里做了双名尝试。

### 美股

yfinance 在不少网络环境下长期 429，因此拆成三个各自独立的小接口：

| 字段 | 接口 | 备注 |
| --- | --- | --- |
| 价格 / 涨跌幅 / 成交额 / PE | 腾讯 `qt.gtimg.cn/q=usAAPL,...` | GBK 编码；`BRK-B` 必须写 `BRK.B` |
| PB | 东财 `push2/ulist.np/get` 批量 | **`f23` 才是 PB×100，`f167` 是无关字段**（曾误用导致 PB 全错） |
| 动量 / 波动 | 新浪 `US_MinKService.getDailyK` | 返回全历史日线，取尾部 21 根 |

东财逐只 `push2` 的并发必须压低：130 个并发请求会让 `push2his` 进入 `RemoteDisconnected` 状态，之后数分钟全挂。

### 加密货币

只用 `https://data-api.binance.vision`（Binance 官方公开数据域）。`api.binance.com`、OKX、火币、Coinbase、Kraken 在部分网络下不可达。K 线时间为 UTC 毫秒，需转 `Asia/Shanghai`。

### 人民币汇率

新浪 `hq.sinajs.cn/list=fx_susdcny`，GBK + 必须带 `Referer: https://finance.sina.com.cn`，取第 9 个字段（索引 8）。

## 因子计算

`src/factors.py` 先算原始值，再对当期横截面做百分位（`rank(pct=True)`），方向相反的因子取 `1 - pct`，最后按 `weights` 加权（权重自动归一化）。

两个必须注意的边界：

- **样本数为 1 时** 百分位恒等于 1.0，会让「越低越好」的因子永远得 0 分 → 特判返回中性 50
- **PE 缺失不等于亏损**：只有 `pe.notna() & (pe <= 0)` 才判定亏损并给 0 分，`NaN` 走中性

## 期权

1. **隐含远期**：先用看跌-看涨平价在 ATM±3% 区间取 `F = K + (C − P)·e^{rT}` 的中位数。股指期权的标的其实是股指期货，不这样处理会得到「Call 13% vs Put 25.7%」的假微笑曲线。
2. **IV 反解**：以 `S = F`、`q = r` 为参数二分求解，结果超出 `[0.005, 2.5]` 视为不收敛，返回 `NaN`。
3. **到期日**：中金所股指期权 = 第三个周五；上交所 ETF 期权 = 第四个周三。默认跳过剩余不足 3 天的近月。
4. **ETF 链取数**：`option_sse_list_sina` → `option_sse_codes_sina`（按看涨/看跌 + 到期月）→ `option_sse_realtime_sina` 逐合约，8 线程并发取，整链约 7 秒。

## 界面细节

- 因子表用 `QTableWidget` + 自定义 `NumericItem.__lt__` 实现按真实数值排序（Qt 默认按字符串排）
- 折线图 X 轴用**等距序号**而非时间轴：真实时间轴会把午休 11:30–13:00 和隔夜画成假斜线
- 表头 tooltip 里挂了权重与口径，评级用徽标样式，缺失值统一灰色 `—`
- 新闻页的设置（来源 / 条数 / 自动刷新间隔）持久化在 `config/ui_state.json`，与 `watchlist.yaml` 解耦，避免污染选股配置

## 启动链路

```
run_windows.bat  → 找到 pythonw.exe → launcher.pyw（无控制台）
                     ├─ 检查 PySide6，缺则静默 pip install
                     ├─ runpy.run_path("desktop_app.py")
                     └─ 异常写 logs/launcher.log 并弹窗
```

`desktop_app.py` 的 `MainWindow.__init__` 不能同步拉行情（会让窗口 25 秒不出现），首次加载走 `QTimer.singleShot(0, self._startup_load)`。
