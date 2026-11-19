"""
数据接入层（A 股 + 美股 + 加密货币）。

设计原则：
- 优先调用行情接口取实时数据；
- 任何一步失败（无网络、接口变动、未安装依赖、被限流）都自动降级到真实代码表，
  只展示代码与名称、行情列留空；内置演示数据已移除。

标准输出列（DataFrame）：
    code            代码
    name            名称
    price           最新价
    change_pct      当日涨跌幅 %
    pe              市盈率（动态 / trailing）
    pb              市净率
    roe             净资产收益率 %（取不到留空，由因子层按中性处理）
    momentum_20d    近 N 日涨跌幅 %
    volatility_20d  近 N 日收益率标准差（越低越稳健）
    turnover        换手率 %（美股无此口径，留空）
    amount          成交额（A 股：亿元；美股：亿美元；加密货币：亿 USDT）
    volume_ratio    量比（美股/加密货币无此口径，留空）
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from concurrent import futures
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd
import yaml

from src import crypto, paths

# 标准列顺序
COLUMNS = [
    "code", "name", "price", "change_pct", "pe", "pb", "roe",
    "momentum_20d", "volatility_20d", "turnover", "amount", "volume_ratio",
]

CONFIG_PATH = paths.resource_path("config/watchlist.yaml")
UNIVERSE_A_PATH = paths.resource_path("data/universe_a.csv")
UNIVERSE_US_PATH = paths.resource_path("data/universe_us.csv")

# 真实代码表列（只有代码与名称，不含行情）
CODE_COLUMNS = ["code", "name"]


def load_config(path: str = CONFIG_PATH) -> Dict:
    """读取 YAML 配置。"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def universe_path(market: str = "A股") -> str:
    """返回该市场的真实代码表路径。"""
    return UNIVERSE_US_PATH if market == "美股" else UNIVERSE_A_PATH


def load_codes(market: str = "A股") -> pd.DataFrame:
    """读取该市场现存真实股票代码表（A 股取自交易所全量清单）。"""
    path = universe_path(market)
    if not os.path.exists(path):
        return pd.DataFrame(columns=CODE_COLUMNS)
    df = pd.read_csv(path, dtype={"code": str})
    df["code"] = df["code"].astype(str).str.strip()
    df["name"] = df["name"].astype(str).str.strip()
    return df[CODE_COLUMNS].dropna().reset_index(drop=True)


def codes_frame(codes: List[str], market: str = "A股") -> pd.DataFrame:
    """代码清单转标准列 DataFrame：只有代码与名称，行情列全空。"""
    name_map = dict(zip(load_codes(market)["code"], load_codes(market)["name"]))
    df = pd.DataFrame({"code": [str(c) for c in codes]})
    df["name"] = df["code"].map(name_map).fillna(df["code"])
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[COLUMNS]


def _to_float(series: pd.Series) -> pd.Series:
    """把可能含 '-' / 空值的列转为数值。"""
    return pd.to_numeric(series.replace({"-": None, "": None}), errors="coerce")


# ===========================================================================
# A 股（akshare / 东方财富）
# ===========================================================================

def _a_share_em_symbol(code: str) -> str:
    """6 位代码转东财财务接口格式：6/9 开头 .SH，其余 .SZ。"""
    code = code.zfill(6)
    suffix = "SH" if code[0] in ("6", "9") else "SZ"
    return f"{code}.{suffix}"


def _fetch_a_roe(code: str) -> Optional[float]:
    """取最新一期 ROE（%）；失败返回 None。"""
    try:
        import akshare as ak
        symbol = _a_share_em_symbol(code)
        df = ak.stock_financial_analysis_indicator_em(symbol=symbol, indicator="按报告期")
        if df is None or df.empty:
            return None
        # 列名可能为“净资产收益率(ROE)”“加权净资产收益率”等，做模糊匹配
        roe_col = next(
            (c for c in df.columns if "净资产收益率" in str(c) or "ROE" in str(c).upper()),
            None,
        )
        if roe_col is None:
            return None
        val = pd.to_numeric(df[roe_col], errors="coerce").dropna()
        if val.empty:
            return None
        return float(val.iloc[0])
    except Exception:
        return None


def _fetch_a_spot(codes: List[str]) -> Optional[pd.DataFrame]:
    """全市场快照按自选股过滤；失败返回 None。"""
    try:
        import akshare as ak
    except Exception:
        return None

    try:
        spot = ak.stock_zh_a_spot_em()
        spot = spot.rename(columns={
            "代码": "code", "名称": "name", "最新价": "price",
            "涨跌幅": "change_pct", "市盈率-动态": "pe", "市净率": "pb",
            "成交额": "amount", "换手率": "turnover", "量比": "volume_ratio",
        })
        spot["code"] = spot["code"].astype(str).str.zfill(6)
        spot = spot[spot["code"].isin(codes)]
        if spot.empty:
            return None

        df = pd.DataFrame({
            "code": spot["code"],
            "name": spot["name"],
            "price": _to_float(spot["price"]),
            "change_pct": _to_float(spot["change_pct"]),
            "pe": _to_float(spot["pe"]),
            "pb": _to_float(spot["pb"]),
            "roe": pd.NA,
            "momentum_20d": pd.NA,
            "volatility_20d": pd.NA,
            "turnover": _to_float(spot["turnover"]),
            "amount": _to_float(spot["amount"]) / 1e8,  # 元 -> 亿元
            "volume_ratio": _to_float(spot["volume_ratio"]),
        })
        return df
    except Exception:
        return None


def _a_hist_frame(code: str) -> Optional[pd.DataFrame]:
    """取 A 股日线历史；兼容 akshare 新旧接口名。"""
    try:
        import akshare as ak
    except Exception:
        return None
    for fn_name in ("stock_zh_a_hist_em", "stock_zh_a_hist"):
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            df = fn(symbol=code, period="daily", adjust="qfq")
            if df is not None and not df.empty:
                return df
        except Exception:
            continue
    return None


def _fetch_a_history(code: str, window: int) -> Dict[str, float]:
    """近 window 日历史行情，计算动量与波动率；失败返回空字典。"""
    try:
        hist = _a_hist_frame(code)
        if hist is None or hist.empty or len(hist) < 5:
            return {}
        close_col = next(
            (c for c in hist.columns if str(c) in ("收盘", "close", "Close")), None
        )
        if close_col is None:
            return {}
        hist = hist.tail(window + 1)
        close = _to_float(hist[close_col]).dropna()
        if len(close) < 5:
            return {}
        momentum = (close.iloc[-1] / close.iloc[0] - 1) * 100
        ret = close.pct_change().dropna()
        volatility = float(ret.std()) if len(ret) > 1 else float("nan")
        return {"momentum_20d": float(momentum), "volatility_20d": volatility}
    except Exception:
        return {}


def _fetch_a_spot_sina(codes: List[str]) -> Optional[pd.DataFrame]:
    """新浪全市场快照兜底（东财被限流时使用）；PE/PB/换手/量比缺失。"""
    try:
        import akshare as ak
    except Exception:
        return None

    try:
        spot = ak.stock_zh_a_spot_sina()
        spot = spot.rename(columns={
            "代码": "code", "名称": "name", "最新价": "price",
            "涨跌幅": "change_pct", "成交额": "amount",
        })
        spot["code"] = (
            spot["code"].astype(str)
            .str.replace(r"^(sh|sz|bj)", "", regex=True)
            .str.zfill(6)
        )
        spot = spot[spot["code"].isin(codes)]
        if spot.empty:
            return None
        return pd.DataFrame({
            "code": spot["code"],
            "name": spot["name"],
            "price": _to_float(spot["price"]),
            "change_pct": _to_float(spot["change_pct"]),
            "pe": pd.NA,
            "pb": pd.NA,
            "roe": pd.NA,
            "momentum_20d": pd.NA,
            "volatility_20d": pd.NA,
            "turnover": pd.NA,
            "amount": _to_float(spot["amount"]) / 1e8,
            "volume_ratio": pd.NA,
        })
    except Exception:
        return None


_A_KLINE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn",
}
_SINA_A_METRICS_CACHE: Dict[str, tuple] = {}


def _sina_symbol(code: str) -> str:
    """6 位代码转新浪代码：6/9 开头 sh，4/8 开头 bj，其余 sz。"""
    code = str(code).zfill(6)
    if code[0] in ("6", "9"):
        return "sh" + code
    if code[0] in ("4", "8"):
        return "bj" + code
    return "sz" + code


def _sina_a_daily_metrics(code: str, window: int = 20) -> Dict[str, float]:
    """用新浪日线算动量与波动；东财被限流时这是唯一可用的历史数据源。

    结果缓存 600 秒，避免同一次刷新重复请求。
    """
    key = f"{code}|{window}"
    hit = _SINA_A_METRICS_CACHE.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    url = (
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
        f"?symbol={_sina_symbol(code)}&scale=240&ma=no&datalen={window + 1}"
    )
    try:
        req = urllib.request.Request(url, headers=_A_KLINE_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read().decode("utf-8", "ignore"))
        if not isinstance(rows, list) or len(rows) < 5:
            return {}
        closes = pd.Series([_scalar_float(r.get("close")) for r in rows], dtype="float64").dropna()
        if len(closes) < 5:
            return {}
        momentum = (closes.iloc[-1] / closes.iloc[0] - 1) * 100
        ret = closes.pct_change().dropna()
        volatility = float(ret.std()) if len(ret) > 1 else float("nan")
        metrics = {"momentum_20d": float(momentum), "volatility_20d": volatility}
        _SINA_A_METRICS_CACHE[key] = (time.time(), metrics)
        return metrics
    except Exception:
        return {}


def _sina_a_metrics_batch(codes: List[str], window: int = 20) -> Dict[str, Dict[str, float]]:
    """并发补齐一批代码的动量/波动。"""
    result: Dict[str, Dict[str, float]] = {}
    if not codes:
        return result
    with futures.ThreadPoolExecutor(max_workers=8) as pool:
        for code, metrics in zip(codes, pool.map(lambda c: _sina_a_daily_metrics(c, window), codes)):
            if metrics:
                result[str(code)] = metrics
    return result


def fetch_a_live(codes: List[str], window: int = 20) -> Optional[pd.DataFrame]:
    """组装 A 股实时行情 + 历史指标 + ROE；整体失败返回 None。"""
    df = _fetch_a_spot(codes)
    fallback = False
    if df is None or df.empty:
        df = _fetch_a_spot_sina(codes)  # 东财限流时退到新浪快照
        fallback = True
    if df is None or df.empty:
        return None

    if fallback:
        # 东财已被限流：逐只历史/ROE 一律走东财会加剧封禁，
        # 这里改用新浪日线补动量与波动（不含 ROE，由因子层按中性处理）。
        metrics_map = _sina_a_metrics_batch(df["code"].astype(str).tolist(), window)
        for idx, row in df.iterrows():
            metrics = metrics_map.get(str(row["code"]))
            if metrics:
                df.at[idx, "momentum_20d"] = metrics["momentum_20d"]
                df.at[idx, "volatility_20d"] = metrics["volatility_20d"]
        return df[COLUMNS].copy()

    for idx, row in df.iterrows():
        metrics = _fetch_a_history(row["code"], window)
        if metrics:
            df.at[idx, "momentum_20d"] = metrics["momentum_20d"]
            df.at[idx, "volatility_20d"] = metrics["volatility_20d"]
        roe = _fetch_a_roe(row["code"])
        if roe is not None:
            df.at[idx, "roe"] = roe
    return df[COLUMNS].copy()


# ===========================================================================
# 美股：腾讯行情为主（一次批量拿全部报价），东财补 PB 与日线，yfinance 兜底
# ===========================================================================

_US_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_EM_MARKET = {"OQ": 105, "N": 106, "AM": 107}  # 纳斯达克 / 纽交所 / 美交所
_US_EXTRAS_CACHE: Dict[str, tuple] = {}


def _scalar_float(value) -> Optional[float]:
    """把单个标量（字符串 / 数字 / None）转为 float；失败返回 None。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num == num else None


def _tx_code(code: str) -> str:
    """配置里常用雅虎写法 BRK-B，腾讯用 BRK.B。"""
    return str(code).strip().upper().replace("-", ".")


def _tx_us_quotes(codes: List[str]) -> Dict[str, Dict]:
    """腾讯批量美股报价：一次请求取完，字段含名称/现价/涨跌幅/成交额/PE/EPS。"""
    if not codes:
        return {}
    url = "https://qt.gtimg.cn/q=" + ",".join("us" + _tx_code(c) for c in codes)
    try:
        req = urllib.request.Request(url, headers=_US_HEADERS)
        raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", "ignore")
    except Exception:
        return {}

    quotes: Dict[str, Dict] = {}
    for line in raw.splitlines():
        if '="' not in line:
            continue
        body = line.split('="', 1)[1].rstrip('";')
        parts = body.split("~")
        if len(parts) < 47:
            continue
        key = line.split("=", 1)[0].replace("v_us", "").strip()
        quotes[key] = {
            "name": parts[1] or key,
            "market": parts[2] or "",         # 例如 AAPL.OQ / BABA.N
            "price": _scalar_float(parts[3]),
            "prev_close": _scalar_float(parts[4]),
            "change_pct": _scalar_float(parts[32]),
            "amount": (_scalar_float(parts[37]) or 0.0) / 1e8,  # 美元 → 亿美元
            "pe": _scalar_float(parts[39]),
            "eps": _scalar_float(parts[46]),
        }
    return quotes


def _em_secid(code: str, market: str) -> str:
    """把腾讯的 AAPL.OQ 形式转成东财 secid（105=纳斯达克 106=纽交所 107=美交所）。"""
    suffix = market.rsplit(".", 1)[-1].upper() if "." in market else "OQ"
    return f"{_EM_MARKET.get(suffix, 105)}.{code}"


def _em_us_pb_batch(items: List[Dict]) -> Dict[str, float]:
    """东财 ulist 批量取市净率：一次请求 40 只，65 只只需 2 次，避免被限流。"""
    hit = _US_EXTRAS_CACHE.get("pb_batch")
    if hit and time.time() - hit[0] < 600:
        return dict(hit[1])

    secids: List[str] = []
    owner: Dict[str, str] = {}
    for item in items:
        code = str(item["code"])
        secid = _em_secid(code, item.get("market", ""))
        secids.append(secid)
        owner[secid] = code

    out: Dict[str, float] = {}
    for start in range(0, len(secids), 40):
        chunk = secids[start:start + 40]
        try:
            url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?secids="
                   + ",".join(chunk) + "&fields=f12,f14,f23")
            raw = urllib.request.urlopen(
                urllib.request.Request(url, headers=_US_HEADERS), timeout=10
            ).read().decode("utf-8", "ignore")
            diff = (json.loads(raw).get("data") or {}).get("diff") or []
            for row in diff:
                # ulist 接口里 f23 = 市净率 ×100（stock/get 接口才是 f167）
                value = _scalar_float(row.get("f23"))
                code = row.get("f12")
                if value and value > 0 and code:
                    out[str(code)] = value / 100.0
        except Exception:
            continue

    _US_EXTRAS_CACHE["pb_batch"] = (time.time(), dict(out))
    return out


def _em_us_pb_single(code: str) -> Optional[float]:
    """单只补 PB（批量接口对少数标的返回空，如 BRK-B / MCD）。

    市场前缀猜错时东财返回 rc:100，这里依次试 105/106/107。
    """
    symbol = _tx_code(code)
    for prefix in (105, 106, 107):
        try:
            url = (f"https://push2.eastmoney.com/api/qt/stock/get?"
                   f"secid={prefix}.{symbol}&fields=f167,f58")
            raw = urllib.request.urlopen(
                urllib.request.Request(url, headers=_US_HEADERS), timeout=8
            ).read().decode("utf-8", "ignore")
            data = json.loads(raw).get("data") or {}
            value = _scalar_float(data.get("f167"))
            if value and value > 0:
                return value / 100.0
        except Exception:
            continue
    return None


def _us_daily_stats(code: str, window: int = 20) -> Dict:
    """新浪美股日线算动量与波动（东财日线易被限流，这里换新浪，带缓存）。"""
    cache_key = f"daily|{code}|{window}"
    hit = _US_EXTRAS_CACHE.get(cache_key)
    if hit and time.time() - hit[0] < 600:
        return dict(hit[1])

    out: Dict = {"momentum_20d": None, "volatility_20d": None}
    try:
        symbol = _tx_code(code)
        url = ("https://stock.finance.sina.com.cn/usstock/api/json_v2.php/"
               f"US_MinKService.getDailyK?symbol={symbol}&___qn=3")
        headers = dict(_US_HEADERS)
        headers["Referer"] = "https://finance.sina.com.cn"
        raw = urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=15
        ).read().decode("utf-8", "ignore")
        start = raw.find("[")
        rows = json.loads(raw[start:]) if start >= 0 else []
        closes = [v for v in (_scalar_float(r.get("c")) for r in rows) if v]
        if len(closes) >= window + 1:
            seg = closes[-(window + 1):]
            out["momentum_20d"] = (seg[-1] / seg[0] - 1) * 100
            rets = pd.Series(seg).pct_change().dropna()
            if len(rets) > 1:
                out["volatility_20d"] = float(rets.std()) * 100
    except Exception:
        pass
    _US_EXTRAS_CACHE[cache_key] = (time.time(), dict(out))
    return out


def _norm_roe(info: Dict) -> Optional[float]:
    """从 yfinance info 取 ROE，统一为百分数；小数口径自动 *100。"""
    raw = info.get("returnOnEquity")
    if raw is None:
        return None
    try:
        val = float(raw)
        # 多数接口返回小数（0.23 -> 23%）；若绝对值已 >1 视为百分数
        return val * 100 if abs(val) <= 1 else val
    except (TypeError, ValueError):
        return None


def _fetch_us_yfinance(us_items: List[Dict], window: int = 20) -> Optional[pd.DataFrame]:
    """yfinance 兜底（常被 429 限流，只在前两个源都拿不到时使用）。"""
    try:
        import yfinance as yf
    except Exception:
        return None

    rows: List[Dict] = []
    any_success = False

    for item in us_items:
        symbol = item["code"]
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=f"{window + 5}d", auto_adjust=False)
            if hist is None or hist.empty or len(hist) < 5:
                continue
            close = hist["Close"].dropna()
            volume = hist["Volume"].dropna()
            info = ticker.info or {}

            momentum = (close.iloc[-1] / close.iloc[0] - 1) * 100
            ret = close.pct_change().dropna()
            volatility = float(ret.std()) if len(ret) > 1 else float("nan")
            change_pct = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) >= 2 else float("nan")
            dollar_volume = float(close.iloc[-1] * volume.iloc[-1]) / 1e8  # 美元 -> 亿美元

            rows.append({
                "code": symbol,
                "name": item.get("name") or info.get("shortName") or symbol,
                "price": float(close.iloc[-1]),
                "change_pct": float(change_pct),
                "pe": info.get("trailingPE"),
                "pb": info.get("priceToBook"),
                "roe": _norm_roe(info),
                "momentum_20d": float(momentum),
                "volatility_20d": volatility,
                "turnover": pd.NA,
                "amount": dollar_volume,
                "volume_ratio": pd.NA,
            })
            any_success = True
        except Exception:
            continue

    if not any_success:
        return None
    return pd.DataFrame(rows, columns=COLUMNS)


def fetch_us_live(us_items: List[Dict], window: int = 20) -> Optional[pd.DataFrame]:
    """组装美股行情 + 估值/质量指标；整体失败返回 None。

    数据源顺序：腾讯批量报价（快、不受 yfinance 限流影响）→ 东财补 PB 与日线
    → 腾讯也拿不到时回落到 yfinance。
    """
    codes = [str(i["code"]) for i in us_items]
    quotes = _tx_us_quotes(codes)

    if quotes:
        # 市值榜（PB）走批量接口，动量/波动走新浪日线，两者都不碰东财逐只接口
        enriched = [dict(i, market=(quotes.get(_tx_code(str(i["code"]))) or {}).get("market", ""))
                    for i in us_items
                    if (quotes.get(_tx_code(str(i["code"]))) or {}).get("price")]
        pb_map = _em_us_pb_batch(enriched)
        missing = [str(i["code"]) for i in enriched if pb_map.get(str(i["code"])) is None]
        if missing:
            with futures.ThreadPoolExecutor(max_workers=4) as pool:
                fixed = {c: pool.submit(_em_us_pb_single, c) for c in missing}
                for code, job in fixed.items():
                    try:
                        value = job.result()
                    except Exception:
                        value = None
                    if value:
                        pb_map[code] = value

        extras: Dict[str, Dict] = {}
        with futures.ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {pool.submit(_us_daily_stats, str(i["code"]), window): str(i["code"])
                    for i in enriched}
            for job in futures.as_completed(jobs):
                code = jobs[job]
                try:
                    extras[code] = job.result()
                except Exception:
                    extras[code] = {}

        rows: List[Dict] = []
        for item in us_items:
            code = str(item["code"])
            q = quotes.get(_tx_code(code))
            if not q or q.get("price") is None:
                continue
            extra = extras.get(code) or {}
            rows.append({
                "code": code,
                "name": item.get("name") or q.get("name") or code,
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
                "pe": q.get("pe"),
                "pb": pb_map.get(code),
                "roe": None,
                "momentum_20d": extra.get("momentum_20d"),
                "volatility_20d": extra.get("volatility_20d"),
                "turnover": pd.NA,
                "amount": q.get("amount"),
                "volume_ratio": pd.NA,
            })
        if rows:
            return pd.DataFrame(rows, columns=COLUMNS)

    return _fetch_us_yfinance(us_items, window=window)


# ===========================================================================
# 统一入口
# ===========================================================================

def get_universe(market: str = "A股", source: str = "auto", window: int = 20,
                 watchlist: Optional[List[Dict]] = None,
                 us_watchlist: Optional[List[Dict]] = None,
                 pool_size: Optional[int] = None,
                 crypto_list: Optional[List[str]] = None) -> pd.DataFrame:
    """
    返回监控股票池 DataFrame（全部来自真实在册代码，不含内置演示数据）。

    参数：
        market       A股 / 美股 / 加密货币
        source       auto / live / codes
        window       动量、波动率回看交易日
        watchlist    A 股配置清单（为空时回落到全市场真实代码表）
        us_watchlist 美股配置清单（同上）
        pool_size    回落代码表时只取前 N 只，None 表示不截断
        crypto_list  加密货币代码清单（BTC/ETH…）

    返回 DataFrame 通过 ``df.attrs["source"]`` 标记真实来源：live / codes / crypto。
    """
    # ---------------- 加密货币：走交易所公开接口，不参与 A股/美股 逻辑
    if market in ("加密", "加密货币"):
        coins = [str(c).strip().upper() for c in (crypto_list or ["BTC"]) if str(c).strip()]

        def _crypto_codes_only() -> pd.DataFrame:
            frame = pd.DataFrame({
                "code": coins,
                "name": [crypto.coin_name(c) for c in coins],
            })
            # 补齐标准列（全部空值），保证下游因子层拿到同样的 dtype
            for col in COLUMNS:
                if col not in frame:
                    frame[col] = float("nan")
            frame.attrs["source"] = "codes"
            return frame[COLUMNS]

        if source in ("codes", "sample"):
            return _crypto_codes_only()
        try:
            live = crypto.fetch_universe(coins, window=window)
        except Exception:
            live = None
        if live is not None and not live.empty:
            return live
        return _crypto_codes_only()

    items = list((us_watchlist if market == "美股" else watchlist) or [])
    codes = [str(i["code"]) for i in items]

    def _codes_only() -> pd.DataFrame:
        use = codes
        if not use:  # 没有配置清单时才回落全市场代码表，并用 pool_size 截断
            use = load_codes(market)["code"].tolist()
            if pool_size:
                use = use[: int(pool_size)]
        frame = codes_frame(use, market)
        if items:
            name_map = {str(i["code"]): i.get("name") for i in items}
            frame["name"] = frame["code"].map(name_map).fillna(frame["name"])
        frame.attrs["source"] = "codes"
        return frame

    if source in ("codes", "sample"):  # sample 为旧配置兼容值
        return _codes_only()

    # 代码清单为空时直接用全市场真实代码表，不做逐只行情抓取
    if not codes:
        return _codes_only()

    live: Optional[pd.DataFrame] = None
    if market == "美股":
        live = fetch_us_live(items, window=window)
    else:
        live = fetch_a_live(codes, window=window)

    if live is not None and not live.empty:
        name_map = {str(i["code"]): i.get("name") for i in items}
        live["name"] = live.apply(
            lambda r: name_map.get(str(r["code"])) or r["name"], axis=1
        )
        live.attrs["source"] = "live"
        return live

    # 行情不可用（限流/无网）时返回真实代码表，不再回落到演示数据
    return _codes_only()
