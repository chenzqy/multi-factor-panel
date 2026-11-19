# 数据源口径与限流应对

> 这份文档是本项目最"脏"的部分——所有接口都是在**中国大陆家庭/办公网络**下实测出来的。
> 写它的目的很简单：换台机器、换个时间点复刻这个项目时，你会遇到一模一样的墙，
> 这里记录的是**每一堵墙在哪、怎么绕过、绕不过时降级成什么**。

数据获取的总体原则：

1. **优先用不鉴权、不限流的公开接口**（新浪、腾讯），把随时会限流的接口（东财 push2）当"加分项"而非"必需品"。
2. **任何一路失败都不许让界面变空**——必须能降级到下一档，最差也要退回「仅代码表」。
3. **同一份数据只拉一次**：所有远程调用都带进程内 TTL 缓存，避免刷新按钮连点把 IP 打进限流池。

代码位置：`src/data_source.py`（行情）、`src/chartdata.py`（K 线）、`src/options.py`（期权）、`src/crypto.py`（加密货币与汇率）、`src/news.py`（新闻）。

---

## 1. 总览

| 用途 | 首选源 | 兜底 | 状态 |
| --- | --- | --- | --- |
| A 股实时报价 | 东财 `stock_zh_a_spot_em` | 新浪 `hq.sinajs.cn` 直连 | 东财**经常**限流，新浪稳定 |
| A 股 PE/PB/ROE/换手 | 东财 `stock_zh_a_spot_em` | 无（留空 → 界面显示「—」） | 仅东财提供，限流期间整列缺 |
| A 股分钟/日 K 线 | 新浪 `CN_MarketDataService.getKLineData` | 东财 `stock_zh_a_hist_em` | 新浪**不限流**，首选 |
| A 股动量/波动 | 新浪日 K 线自算 | 东财历史接口 | 逐只拉日线，必须并发 + 缓存 |
| 美股报价/PE | 腾讯 `qt.gtimg.cn` 批量 | yfinance（基本 429） | 腾讯稳定，0.1~0.3s |
| 美股 PB | 东财 `ulist.np/get` 批量 | 无（缺 5 只） | 批量接口不限流 |
| 美股动量/波动 | 新浪 `US_MinKService.getDailyK` | 东财 `push2his`（必限流） | 新浪返回全历史，取尾部 |
| 加密货币 | Binance `data-api.binance.vision` | 无 | 主站与所有其他交易所均超时 |
| 股指/ETF 期权链 | 新浪 CFFEX / SSE 接口 | 无 | 东财期权接口全限流 |
| 美元兑人民币 | 新浪 `fx_susdcny` | 无 | 稳定，60s 缓存 |
| 财经快讯 | 东财 `stock_info_global_em` + 财联社 | 本地样例 | 稳定 |

---

## 2. A 股

### 2.1 三级降级链

```
东财 stock_zh_a_spot_em（全字段，含 PE/PB/ROE/换手/量比）
    ↓ 失败（Push2RateLimitError / 超时）
新浪 hq.sinajs.cn 批量（只有价格、涨跌幅、成交额）
    ↓ 失败
data/universe_a.csv（5580 只真实代码，价格列全空）
```

界面左上角「数据源」下拉即对应这三档：`自动 / 实时行情 / 仅代码表`，状态栏会写明当前落到哪一档。

### 2.2 东财限流

`stock_zh_a_spot_em` 打的是 `push2.eastmoney.com`，触发限流时 akshare 抛：

```
Push2RateLimitError: ... 当前 IP 被临时限流 ...
首次限流一般约 5 分钟后恢复，反复触发后可能需要 40 分钟以上
```

**应对**：不要在限流期间反复重试（只会延长封禁）。本项目在失败时直接切新浪，并把 PE/PB/ROE 列标成「—」，等下一次刷新自然恢复。

### 2.3 新浪批量报价

```http
GET https://hq.sinajs.cn/list=sh600519,sz000001,...
Referer: https://finance.sina.com.cn
```

- 返回 **GBK**，必须显式解码；
- **必须带 Referer**，否则返回空串或 403；
- 一批可以带几十个代码，但太长会截断，本项目按 60 个一批切分；
- 字段（逗号分隔）：`0 名称, 1 今开, 2 昨收, 3 现价, 4 最高, 5 最低, ...8 成交量(手), 9 成交额(元), 30 日期, 31 时间`；
- 代码前缀规则：6/9 开头 → `sh`，4/8 开头 → `bj`，其余 → `sz`。

### 2.4 新浪 K 线（分钟 + 日线，同一个接口）

```http
GET https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData
    ?symbol=sh600519&scale=5&ma=no&datalen=60
Referer: https://finance.sina.com.cn
```

- `scale`：`5 / 15 / 30 / 60` 为分钟线，`240` 为日线；
- `datalen`：返回根数，日线给 121 根即可算 20 日动量与波动率；
- 返回：`[{"day":"2026-11-19 09:35:00","open":"…","high":"…","low":"…","close":"…","volume":"…"}, …]`；
- **不限流**，是这个项目里最可靠的数据通道，分时图与动量/波动因子都靠它。

### 2.5 历史日线接口改名

akshare ≥ 1.20.46 起，`stock_zh_a_hist` 改名为 **`stock_zh_a_hist_em`**，调用旧名会抛：

```
AttributeError: stock_zh_a_hist 已在 1.20.46 更名为 stock_zh_a_hist_em
```

代码里按 `("stock_zh_a_hist_em", "stock_zh_a_hist")` 顺序 `getattr` 兼容旧版本。

---

## 3. 美股

三源拼装，各管一段：

### 3.1 腾讯报价（价格 / PE / 成交额）

```http
GET https://qt.gtimg.cn/q=usAAPL,usMSFT,usBRK.B
```

- **GBK** 编码；一次可查几十只；
- 返回形如 `v_usAAPL="200~苹果~AAPL.OQ~352.45~351.91~…"`；
- 字段速查：`[1] 名称, [2] 代码+市场后缀(AAPL.OQ / BABA.N / SPY.AM), [3] 现价, [4] 昨收, [5] 今开, [30] 时间, [32] 涨跌幅%, [37] 成交额(美元), [39] PE(TTM), [46] EPS`；
- **坑**：雅虎写法 `BRK-B` 必须换成 `BRK.B`，否则取不到数据（代码里 `_tx_code()` 负责转换）。

### 3.2 东财批量 PB

```http
GET https://push2.eastmoney.com/api/qt/ulist.np/get
    ?secids=105.AAPL,106.JPM,105.MSFT&fields=f12,f14,f23
```

- `secid` 前缀：`105` = 纳斯达克，`106` = 纽交所，`107` = 美交所；
- **致命坑**：ulist 接口里 **`f23` 才是市净率 ×100**，`f167` 是另一个字段。
  早期版本误用 `f167`，导致 NVDA 的 PB 显示成 `0.04`；
- 每 40 只一批，2 个请求就能覆盖 65 只美股，**ulist 批量接口基本不限流**（被限流的是 `push2his` 历史接口）；
- 个别标的（BRK.B、ABBV、MCD、PM、BKNG）东财不返回 PB，界面显示「—」，这是数据源缺失，不是 bug。

### 3.3 新浪美股全历史日线（动量 / 波动）

```http
GET https://stock.finance.sina.com.cn/usstock/api/json_v2.php/US_MinKService.getDailyK
    ?symbol=AAPL&___qn=3
Referer: https://finance.sina.com.cn
```

- 返回**全历史**，约 900KB / 只：`[{"d":"1984-09-07","o":…,"h":…,"l":…,"c":…,"v":…}, …]`；
- 只取尾部 21 根算 20 日动量与日收益标准差；
- 单只 0.4~0.8s，必须**并发 + 缓存**（本项目 `ThreadPoolExecutor(max_workers=6)` + 600s TTL）；
- 价格与腾讯段一致，可互为校验。

### 3.4 已知不可用

| 通道 | 现象 |
| --- | --- |
| yfinance | `YFRateLimitError: Too Many Requests`，本网络几乎必 429 |
| 新浪美股**分钟**线 | 服务已下线：`{"__ERROR":3,"Service not valid"}` |
| 腾讯美股 K 线 | `web.ifzq.gtimg.cn` 的 usfqkline 只返回 2 根（2011-06-02 与最近一天） |
| 东财 `push2his` 逐只历史 | 并发一高立刻 `RemoteDisconnected`，之后数分钟全挂 |
| stooq | 需要 JS 校验，直接抓返回 HTML |

**结论**：美股折线图只能给日线，没有分时。

---

## 4. 加密货币

### 4.1 唯一可直连的域名

```
https://data-api.binance.vision
```

中国大陆网络实测：`api.binance.com`、`www.okx.com`、`aws.okx.com`、`api.huobi.pro`、`api.coinbase.com`、`api.bitstamp.net`、`api.kraken.com`、CoinGecko **全部超时**。只有这个 Binance 公开数据域名稳定可用（无需 API Key）。

### 4.2 接口

```http
GET https://data-api.binance.vision/api/v3/ticker/24hr?symbol=BTCUSDT
GET https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=48
```

- K 线返回数组：`[openTime(UTC 毫秒), open, high, low, close, volume, closeTime, quoteVolume, trades, ...]`；
- 时间戳必须 `pd.to_datetime(ms, unit="ms", utc=True).tz_convert("Asia/Shanghai").tz_localize(None)`；
- 24h 成交额取 `quoteVolume`（USDT 计价），除以 1e8 得到「亿」；
- 加密市场 **7×24 交易**，分时图不能按"最后交易日"裁剪，要跨日连续显示。

### 4.3 单标的打分的坑

横截面百分位打分在**样本数 = 1** 时，`rank(pct=True)` 恒等于 1.0，会让"越低越好"的因子（波动、估值）恒得 0 分。
`factors._percentile_score()` 里对 `s.notna().sum() <= 1` 直接返回中性 50 分。

---

## 5. 期权

### 5.1 数据源：全走新浪

东财期权接口（`option_realtime_em`，旧名 `option_current_em`）在本网络全部限流，本项目改用新浪：

**中金所股指期权**（IO / HO / MO）

```python
ak.option_cffex_hs300_list_sina()        # → 列 ['品种','合约']，如 io2611 / io2612
ak.option_cffex_hs300_realtime_sina(symbol="io2612")
```

一次返回**整条链**（31 行 × 17 列），关键列名：
`看涨合约-买价 / 最新价 / 卖价 / 持仓量 / 涨跌`、`行权价`、`看跌合约-*`。

**上交所 ETF 期权**（510050 / 510300 / 510500 / 588000）

```python
ak.option_sse_list_sina(symbol='510050')                                   # → 到期月份 ['202611', ...]
ak.option_sse_codes_sina(symbol='看涨期权', trade_date='202611', underlying='510050')  # → 合约代码列表
ak.option_sse_realtime_sina(symbol='10012637')                             # → 单合约 字段/值 长表
```

ETF 期权的合约是**逐个**查的，12 个行权价 × 2 方向 = 24 次请求，必须 8 线程并发（本项目实测 7.3s 出表）。

**标的现价**

- 股指：`ak.index_zh_spot_sina()`（sh000300 / sh000016 / sh000852），注意它会打 tqdm 进度条（70 次请求），必须 20s 缓存；
- ETF：`https://hq.sinajs.cn/list=sh510050`（带 Referer，GBK，第 4 个字段是现价）。

### 5.2 定价最大的坑：股指期权的标的是期货

中金所股指期权（IO/HO/MO）的行权价对应的是**股指期货**，不是沪深 300 现货。
直接用 `sh000300` 现价算隐含波动率，会得到严重不对称的结果（实测 Call 13% / Put 25.7%，且 IV 随行权价线性漂移）。

正确做法：先用**看跌-看涨平价**反解隐含远期 `F`，再以 `S = F`、`q = r` 定价：

```
F = K + (C - P) · e^{rT}      （取 ATM ±3% 各行权价的中位数）
```

修正后 io2612 全链 Call/Put IV 对齐在 19% ±1（ATM 3950 两侧均 19.2%），微笑曲线恢复正常。

`fetch_option_chain()` 会把反解出的 `F` 挂在 `DataFrame.attrs["forward"]` 上。

### 5.3 到期日

| 品种 | 到期规则 |
| --- | --- |
| 中金所股指期权 | 合约月**第三个周五** |
| 上交所 ETF 期权 | 合约月**第四个周三** |

远端/近月筛选：`_default_expiry()` 自动跳过剩余不足 3 天的近月（否则一打开就是即将到期、流动性枯竭的链）。

### 5.4 IV 为何会显示「—」

深度虚值合约最新价可能只有 0.0004，反解出的 IV 小于 0.5% 或大于 250% 时判定无意义，返回 `nan` → 界面显示「—」。这是**正确的**，不是取数失败。

---

## 6. 汇率

```http
GET https://hq.sinajs.cn/list=fx_susdcny
Referer: https://finance.sina.com.cn
```

- GBK 编码，返回 `var hq_str_fx_susdcny="时间,买价,卖价,昨收,…,今开,最高,最低,现价,…"`；
- **现价取第 8 个字段**（`parts[8]`），实测 6.665；
- `fx_susdcnh` 是离岸人民币（CNH），本项目用**在岸** CNY；
- 60s 缓存；取不到时「折人民币(¥)」列显示「—」，界面概览条显示「汇率暂缺」，不阻塞行情刷新。

---

## 7. 新闻

- `ak.stock_info_global_em()`：东方财富全球快讯；
- `ak.stock_info_global_cls()`：财联社电报；
- 两者都是 `标题 / 摘要 / 时间 / 链接` 四列，本项目做了字段归一并按时间倒序；
- 可扩展 RSS（`config/watchlist.yaml` 的 `news.rss_feeds`），走 `feedparser`；
- 全部失败时退回本地样例，界面上明确标注「样例数据」。

---

## 8. 缓存与并发一览

| 缓存键 | TTL | 说明 |
| --- | --- | --- |
| A 股实时报价 | 无（手动刷新） | 一次刷新一个请求 |
| 新浪日线因子 | 600s | `_SINA_A_METRICS_CACHE`，按 `code|window` |
| 美股日线因子 | 600s | 按 `daily|code|window` |
| 美股 PB 批量 | 600s | 按 `pb_batch` |
| 期权整链 | 20s | `options._CACHE` |
| 加密货币 | 20s | `crypto.CACHE_TTL` |
| 汇率 | 60s | `crypto.FX_TTL` |

并发模型：所有"逐只"接口（新浪日线、ETF 合约）统一用 `ThreadPoolExecutor`，
并发度 6~8，**不要更高**——东财对并发极其敏感，而新浪虽然宽容，但并发过高一样会被丢掉连接。

**一条硬规则**：`push2his.eastmoney.com` 每多 10 个并发就多一档限流风险，
本项目历史上 130 个并发请求把该域名打到 `RemoteDisconnected('Remote end closed connection without response')`，
之后数分钟所有东财接口全挂。

---

## 9. 复刻时的检查清单

1. `hq.sinajs.cn` 与 `quotes.sina.cn` 的请求**必须带 Referer**，且按 GBK 解码；
2. 东财字段以 `f` 开头的一律按"×100 的整数"处理（`f23` = PB×100，`f43` = 价格×100…），先除再显示；
3. 任何"逐只"接口都要并发 + 缓存，且并发度压到 8 以内；
4. 降级链的每一档都要能在界面上看出来（本项目用状态栏 tag + 概览条的「数据源」字样）；
5. 免费源缺字段是常态，界面显示「—」比显示 0 或假值更诚实。
