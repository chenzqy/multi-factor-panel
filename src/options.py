"""
期权观察模块（真实行情版）。

数据来源（均为新浪公开行情，不依赖东方财富 push2，避免限流）：
- 中金所股指期权：沪深300(IO) / 上证50(HO) / 中证1000(MO)
  * 合约列表：option_cffex_hs300_list_sina / option_cffex_sz50_list_sina / option_cffex_zz1000_list_sina
  * 整条链行情：option_cffex_*_realtime_sina(symbol=合约)，一次返回全部行权价的 Call/Put 买卖价、最新价、持仓量
- 上交所 ETF 期权：510050 / 510300 / 510500 / 588000
  * 合约代码：option_sse_codes_sina(underlying=标的, trade_date=YYYYMM)
  * 单合约行情：option_sse_realtime_sina(symbol=合约代码)
- 标的现价：index_zh_spot_sina（指数）/ 新浪 A 股快照（ETF）

在真实价基础上反解隐含波动率，并用 Black-Scholes 补齐 Delta/Gamma/Theta/Vega，
所以面板既展示市场真实报价，也给出理论定价对照。

缓存 TTL 默认 20 秒，避免频繁刷新被新浪限流。
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------- 常量

RISK_FREE = 0.02          # 无风险年化利率，用于定价与 IV 反解
CACHE_TTL = 20            # 秒
CHAIN_COLUMNS = [
    "行权价", "虚实值",
    "Call最新价", "Call买价", "Call卖价", "Call持仓量",
    "Call隐含波动率", "Call Delta",
    "Put最新价", "Put买价", "Put卖价", "Put持仓量",
    "Put隐含波动率", "Put Delta",
    "平价偏离",
]

# 市场定义：key -> 元数据
MARKETS: Dict[str, Dict] = {
    "io": {
        "name": "沪深300股指期权(IO)", "kind": "cffex", "prefix": "io",
        "list_fn": "option_cffex_hs300_list_sina",
        "chain_fn": "option_cffex_hs300_realtime_sina",
        "index": "sh000300", "spot_name": "沪深300指数", "multiplier": 100,
    },
    "ho": {
        "name": "上证50股指期权(HO)", "kind": "cffex", "prefix": "ho",
        "list_fn": "option_cffex_sz50_list_sina",
        "chain_fn": "option_cffex_sz50_realtime_sina",
        "index": "sh000016", "spot_name": "上证50指数", "multiplier": 100,
    },
    "mo": {
        "name": "中证1000股指期权(MO)", "kind": "cffex", "prefix": "mo",
        "list_fn": "option_cffex_zz1000_list_sina",
        "chain_fn": "option_cffex_zz1000_realtime_sina",
        "index": "sh000852", "spot_name": "中证1000指数", "multiplier": 100,
    },
    "510050": {
        "name": "上证50ETF期权(510050)", "kind": "etf", "underlying": "510050",
        "spot_name": "50ETF", "multiplier": 10000,
    },
    "510300": {
        "name": "沪深300ETF期权(510300)", "kind": "etf", "underlying": "510300",
        "spot_name": "300ETF", "multiplier": 10000,
    },
    "510500": {
        "name": "中证500ETF期权(510500)", "kind": "etf", "underlying": "510500",
        "spot_name": "500ETF", "multiplier": 10000,
    },
    "588000": {
        "name": "科创50ETF期权(588000)", "kind": "etf", "underlying": "588000",
        "spot_name": "科创50ETF", "multiplier": 10000,
    },
}

_CACHE: Dict[str, Tuple[float, object]] = {}


def _cache_get(key: str):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    return None


def _cache_put(key: str, value):
    _CACHE[key] = (time.time(), value)
    return value


def clear_cache() -> None:
    _CACHE.clear()


# ---------------------------------------------------------------- 定价

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    """标准正态分布 CDF（基于 erf，避免额外依赖 scipy）。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes(S: float, K: float, T: float, r: float,
                  sigma: float, option_type: str = "call",
                  q: float = 0.0) -> Dict[str, float]:
    """Black-Scholes 定价与希腊字母。"""
    T = max(T, 1e-6)
    sigma = max(sigma, 1e-6)
    if S <= 0 or K <= 0:
        return {"price": float("nan"), "delta": float("nan"), "gamma": float("nan"),
                "theta": float("nan"), "vega": float("nan")}

    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    disc = math.exp(-r * T)

    if option_type == "call":
        price = S * math.exp(-q * T) * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
        delta = math.exp(-q * T) * _norm_cdf(d1)
        theta = (-(S * math.exp(-q * T) * _norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
                 - r * K * disc * _norm_cdf(d2)
                 + q * S * math.exp(-q * T) * _norm_cdf(d1)) / 365.0
    else:
        price = K * disc * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)
        delta = math.exp(-q * T) * (_norm_cdf(d1) - 1)
        theta = (-(S * math.exp(-q * T) * _norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
                 + r * K * disc * _norm_cdf(-d2)
                 - q * S * math.exp(-q * T) * _norm_cdf(-d1)) / 365.0

    gamma = math.exp(-q * T) * _norm_pdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * math.exp(-q * T) * _norm_pdf(d1) * math.sqrt(T) / 100.0

    return {
        "price": round(max(price, 0.0), 6),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
    }


def implied_vol(price: float, S: float, K: float, T: float,
                r: float = RISK_FREE, option_type: str = "call",
                lo: float = 0.001, hi: float = 3.0, tol: float = 1e-6) -> float:
    """由市场价格反解隐含波动率（二分法）；无解返回 nan。"""
    if not (price and price > 0 and S > 0 and K > 0 and T > 0):
        return float("nan")
    intrinsic = max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
    if price <= intrinsic * 0.999:
        return float("nan")
    result = float("nan")
    for _ in range(80):
        mid = (lo + hi) / 2.0
        val = black_scholes(S, K, T, r, mid, option_type, q=r)["price"]
        if abs(val - price) < tol:
            result = mid
            break
        if val > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-5:
            break
    if result != result:
        result = (lo + hi) / 2.0
    result = round(result, 4)
    return result if 0.005 < result < 2.5 else float("nan")


def build_option_chain(spot: float, strike_step: float, strikes_around: int,
                       T: float, r: float, sigma: float) -> pd.DataFrame:
    """纯理论期权链（无行情时的对照口径）。"""
    atm = round(spot / strike_step) * strike_step
    strikes = [round(atm + i * strike_step, 2)
               for i in range(-strikes_around, strikes_around + 1)]
    rows = []
    for K in strikes:
        moneyness = "平值" if abs(K - spot) < 1e-6 else ("实值(Call)" if K < spot else "虚值(Call)")
        call = black_scholes(spot, K, T, r, sigma, "call")
        put = black_scholes(spot, K, T, r, sigma, "put")
        rows.append({
            "行权价": K, "虚实值": moneyness,
            "Call理论价": call["price"], "Call Delta": call["delta"],
            "Call Gamma": call["gamma"],
            "Put理论价": put["price"], "Put Delta": put["delta"],
            "Put Gamma": put["gamma"],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 到期日

def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    offset = (4 - d.weekday()) % 7      # 周五 = 4
    return date(year, month, 1 + offset + 14)


def _fourth_wednesday(year: int, month: int) -> date:
    d = date(year, month, 1)
    offset = (2 - d.weekday()) % 7      # 周三 = 2
    return date(year, month, 1 + offset + 21)


def expiry_info(market_key: str, expiry: str) -> Tuple[date, int, float]:
    """返回 (到期日, 剩余自然日, 剩余年限)。"""
    meta = MARKETS[market_key]
    digits = "".join(ch for ch in str(expiry) if ch.isdigit())
    if len(digits) == 6:                      # 202611
        year, month = int(digits[:4]), int(digits[4:])
    elif len(digits) == 4:                    # 2611
        year, month = 2000 + int(digits[:2]), int(digits[2:])
    else:
        year, month = date.today().year, date.today().month

    if meta["kind"] == "cffex":
        exp = _third_friday(year, month)
    else:
        exp = _fourth_wednesday(year, month)
    days = max((exp - date.today()).days, 0)
    return exp, days, max(days / 365.0, 1.0 / 365.0)


# ---------------------------------------------------------------- 市场与合约

def list_markets() -> List[Dict[str, str]]:
    """可选市场列表：[{key, name}]。"""
    return [{"key": k, "name": v["name"]} for k, v in MARKETS.items()]


def list_expiries(market_key: str) -> List[str]:
    """可用到期月份/合约列表（升序）。"""
    meta = MARKETS.get(market_key)
    if not meta:
        return []
    cached = _cache_get(f"exp:{market_key}")
    if cached is not None:
        return cached  # type: ignore[return-value]

    import akshare as ak
    out: List[str] = []
    try:
        if meta["kind"] == "cffex":
            df = getattr(ak, meta["list_fn"])()
            col = "合约" if "合约" in df.columns else df.columns[-1]
            out = [str(x) for x in df[col].tolist()]
        else:
            df = ak.option_sse_list_sina(symbol=meta["underlying"], exchange="null")
            col = "到期月份" if "到期月份" in df.columns else df.columns[0]
            out = [str(x) for x in df[col].tolist()]
    except Exception:
        out = []

    def _sort_key(s: str):
        d = "".join(ch for ch in s if ch.isdigit())
        return d

    out = sorted(set(out), key=_sort_key)
    return _cache_put(f"exp:{market_key}", out)  # type: ignore[return-value]


def _sina_hq(code: str) -> Optional[float]:
    """新浪实时快照（hq.sinajs.cn），code 形如 sh510050 / sh000300。"""
    try:
        import requests
        url = f"https://hq.sinajs.cn/list={code}"
        resp = requests.get(
            url,
            headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"},
            timeout=6,
        )
        body = resp.text.split('="', 1)[1].rsplit('"', 1)[0]
        parts = [p.strip() for p in body.split(",")]
        for idx in (3, 2):                      # 3=当前价, 2=昨收
            try:
                val = float(parts[idx])
            except (IndexError, ValueError):
                continue
            if val > 0:
                return val
    except Exception:
        return None
    return None


def spot_price(market_key: str) -> Optional[float]:
    """标的现价：指数/ETF 优先新浪实时快照，失败回落指数快照或日线收盘。"""
    meta = MARKETS.get(market_key)
    if not meta:
        return None
    cached = _cache_get(f"spot:{market_key}")
    if cached is not None:
        return cached  # type: ignore[return-value]

    import akshare as ak
    price: Optional[float] = None
    try:
        if meta["kind"] == "cffex":
            price = _sina_hq(meta["index"])
            if not price:
                df = ak.index_zh_spot_sina()
                row = df[df["代码"].astype(str) == meta["index"]]
                if not row.empty:
                    price = float(row.iloc[0]["最新价"])
        else:
            code = str(meta["underlying"])
            prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
            price = _sina_hq(prefix + code)
            if not price:
                hist = ak.fund_etf_hist_sina(symbol=prefix + code)
                if hist is not None and not hist.empty:
                    price = float(hist.iloc[-1]["close"])
    except Exception:
        price = None

    if price:
        _cache_put(f"spot:{market_key}", price)
    return price


# ---------------------------------------------------------------- 期权链

def _to_num(series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _mid(bid, ask, last) -> float:
    """优先买卖中值，缺一侧则用最新价。"""
    try:
        b, a = float(bid), float(ask)
        if b > 0 and a > 0:
            return round((b + a) / 2.0, 6)
    except (TypeError, ValueError):
        pass
    try:
        v = float(last)
        return v if v > 0 else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def _implied_forward(chain: pd.DataFrame, spot: Optional[float], T: float,
                     r: float = RISK_FREE) -> Optional[float]:
    """
    用看跌-看涨平价反推隐含远期：F = K + (C - P) * e^{rT}。

    股指期权（IO/HO/MO）的真实标的是对应股指期货，直接用现货指数当 S 会让
    Call/Put 隐含波动率严重不对称；ETF 期权含分红同理。取 ATM 附近中位数最稳。
    """
    cands = []
    for _, row in chain.iterrows():
        try:
            K = float(row["行权价"])
        except (TypeError, ValueError):
            continue
        c = _mid(row["Call买价"], row["Call卖价"], row["Call最新价"])
        p = _mid(row["Put买价"], row["Put卖价"], row["Put最新价"])
        if c == c and p == p and c > 0 and p > 0:
            cands.append((K, K + (c - p) * math.exp(r * T)))
    if not cands:
        return None
    near = [f for k, f in cands if spot and spot > 0 and abs(k - spot) / spot <= 0.03]
    pool = near if near else [f for _, f in cands]
    pool = [f for f in pool if f > 0]
    if not pool:
        return None
    pool.sort()
    mid = len(pool) // 2
    return round(pool[mid] if len(pool) % 2 else (pool[mid - 1] + pool[mid]) / 2.0, 4)


def _decorate(df: pd.DataFrame, spot: Optional[float], T: float,
              r: float = RISK_FREE) -> pd.DataFrame:
    """补虚实值、隐含波动率、Delta 与期权平价偏离（用隐含远期定价）。"""
    out = df.copy()
    forward = _implied_forward(out, spot, T, r)
    S_eff = forward if forward else spot
    out.attrs["forward"] = forward

    for side in ("Call", "Put"):
        ivs, deltas = [], []
        for _, row in out.iterrows():
            K = float(row["行权价"])
            px = _mid(row[f"{side}买价"], row[f"{side}卖价"], row[f"{side}最新价"])
            if S_eff and S_eff > 0 and px == px and px > 0:
                iv = implied_vol(px, S_eff, K, T, r, side.lower())
                # 期货期权：S=F 且 q=r
                if iv == iv:
                    deltas.append(black_scholes(S_eff, K, T, r, iv,
                                                side.lower(), q=r)["delta"])
                else:
                    deltas.append(float("nan"))
            else:
                iv, _d = float("nan"), float("nan")
                deltas.append(float("nan"))
            ivs.append(iv)
        out[f"{side}隐含波动率"] = ivs
        out[f"{side} Delta"] = deltas

    ref = spot if spot else S_eff
    if ref and ref > 0:
        out["虚实值"] = [
            "平值" if abs(float(k) - ref) <= (ref * 0.005)
            else ("实值" if float(k) < ref else "虚值")
            for k in out["行权价"]
        ]
        call_mid = out.apply(lambda r_: _mid(r_["Call买价"], r_["Call卖价"], r_["Call最新价"]), axis=1)
        put_mid = out.apply(lambda r_: _mid(r_["Put买价"], r_["Put卖价"], r_["Put最新价"]), axis=1)
        out["平价偏离"] = [
            round(float(c) - float(p) - (ref - float(k)), 4)
            if c == c and p == p else float("nan")
            for c, p, k in zip(call_mid, put_mid, out["行权价"])
        ]
    else:
        out["虚实值"] = ""
        out["平价偏离"] = float("nan")
    return out


def _chain_cffex(market_key: str, expiry: str) -> pd.DataFrame:
    meta = MARKETS[market_key]
    import akshare as ak
    symbol = str(expiry)
    if not symbol.lower().startswith(meta["prefix"]):
        digits = "".join(ch for ch in str(expiry) if ch.isdigit())
        if len(digits) == 6:
            symbol = meta["prefix"] + digits[2:]
        elif len(digits) == 4:
            symbol = meta["prefix"] + digits
    df = getattr(ak, meta["chain_fn"])(symbol=symbol)
    if df is None or df.empty:
        return pd.DataFrame(columns=CHAIN_COLUMNS)

    out = pd.DataFrame({
        "行权价": _to_num(df["行权价"]),
        "Call最新价": _to_num(df["看涨合约-最新价"]),
        "Call买价": _to_num(df["看涨合约-买价"]),
        "Call卖价": _to_num(df["看涨合约-卖价"]),
        "Call持仓量": _to_num(df["看涨合约-持仓量"]),
        "Put最新价": _to_num(df["看跌合约-最新价"]),
        "Put买价": _to_num(df["看跌合约-买价"]),
        "Put卖价": _to_num(df["看跌合约-卖价"]),
        "Put持仓量": _to_num(df["看跌合约-持仓量"]),
    })
    return out.dropna(subset=["行权价"]).sort_values("行权价").reset_index(drop=True)


def _etf_contract_map(underlying: str, expiry: str, side: str) -> Dict[float, str]:
    """ETF 期权：行权价 -> 合约代码。"""
    import akshare as ak
    code_df = ak.option_sse_codes_sina(
        symbol="看涨期权" if side == "call" else "看跌期权",
        trade_date=str(expiry), underlying=str(underlying),
    )
    codes = [str(c) for c in code_df.iloc[:, -1].tolist()]
    mapping: Dict[float, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        frames = list(pool.map(lambda c: _sse_quote(c), codes))
    for code, row in zip(codes, frames):
        if row is None:
            continue
        mapping[row["行权价"]] = code
    return mapping


def _sse_quote(code: str) -> Optional[Dict[str, float]]:
    """单只 ETF 期权合约行情（字段/值 长表转字典）。"""
    import akshare as ak
    try:
        raw = ak.option_sse_realtime_sina(symbol=str(code))
        kv = dict(zip(raw["字段"].astype(str), raw["值"]))
    except Exception:
        return None

    def num(name: str) -> float:
        try:
            return float(str(kv.get(name, "")).replace(",", ""))
        except (TypeError, ValueError):
            return float("nan")

    strike = num("行权价")
    if strike != strike:
        return None
    return {
        "行权价": strike,
        "最新价": num("最新价"),
        "买价": num("买价"),
        "卖价": num("卖价"),
        "持仓量": num("持仓量"),
    }


def _chain_etf(market_key: str, expiry: str) -> pd.DataFrame:
    meta = MARKETS[market_key]
    underlying, key = meta["underlying"], str(expiry)
    calls = _cache_get(f"etfC:{underlying}:{key}")
    puts = _cache_get(f"etfP:{underlying}:{key}")
    if calls is None:
        calls = _etf_quotes(underlying, key, "call")
        _cache_put(f"etfC:{underlying}:{key}", calls)
    if puts is None:
        puts = _etf_quotes(underlying, key, "put")
        _cache_put(f"etfP:{underlying}:{key}", puts)

    if not calls and not puts:
        return pd.DataFrame(columns=CHAIN_COLUMNS)
    strikes = sorted(set(calls) | set(puts))
    rows = []
    for K in strikes:
        c = calls.get(K, {})
        p = puts.get(K, {})
        rows.append({
            "行权价": K,
            "Call最新价": c.get("最新价", float("nan")),
            "Call买价": c.get("买价", float("nan")),
            "Call卖价": c.get("卖价", float("nan")),
            "Call持仓量": c.get("持仓量", float("nan")),
            "Put最新价": p.get("最新价", float("nan")),
            "Put买价": p.get("买价", float("nan")),
            "Put卖价": p.get("卖价", float("nan")),
            "Put持仓量": p.get("持仓量", float("nan")),
        })
    return pd.DataFrame(rows)


def _etf_quotes(underlying: str, expiry: str, side: str) -> Dict[float, Dict[str, float]]:
    import akshare as ak
    try:
        code_df = ak.option_sse_codes_sina(
            symbol="看涨期权" if side == "call" else "看跌期权",
            trade_date=str(expiry), underlying=str(underlying),
        )
        codes = [str(c) for c in code_df.iloc[:, -1].tolist()]
    except Exception:
        return {}
    out: Dict[float, Dict[str, float]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for row in pool.map(_sse_quote, codes):
            if row:
                out[row["行权价"]] = row
    return out


def _default_expiry(market_key: str, seasons: List[str]) -> str:
    """默认选最近一个剩余 3 天以上的合约，避免临到期噪声。"""
    if not seasons:
        return ""
    for s in seasons:
        try:
            if expiry_info(market_key, s)[1] >= 3:
                return s
        except Exception:
            continue
    return seasons[0]


def fetch_option_chain(market_key: str = "io", expiry: str = "",
                       spot: Optional[float] = None,
                       with_greeks: bool = True) -> pd.DataFrame:
    """
    拉取真实期权链。

    参数：
        market_key   市场 key（见 MARKETS）
        expiry       到期合约/月份；空则取最近一个
        spot         标的现价；空则自动获取
        with_greeks  是否补隐含波动率与 Delta
    返回：CHAIN_COLUMNS 结构的 DataFrame（真实买卖价 + 持仓量 + IV）
    """
    if market_key not in MARKETS:
        return pd.DataFrame(columns=CHAIN_COLUMNS)

    seasons = list_expiries(market_key)
    if not seasons:
        return pd.DataFrame(columns=CHAIN_COLUMNS)
    if not expiry:
        expiry = _default_expiry(market_key, seasons)

    cache_key = f"chain:{market_key}:{expiry}:{int(bool(with_greeks))}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached.copy()  # type: ignore[union-attr]

    meta = MARKETS[market_key]
    chain = _chain_cffex(market_key, expiry) if meta["kind"] == "cffex" \
        else _chain_etf(market_key, expiry)
    if chain.empty:
        return chain

    if with_greeks:
        if spot is None:
            spot = spot_price(market_key)
        _, _, T = expiry_info(market_key, expiry)
        chain = _decorate(chain, spot, T)

    cols = [c for c in CHAIN_COLUMNS if c in chain.columns]
    chain = chain[cols]
    _cache_put(cache_key, chain)
    return chain.copy()


def chain_summary(market_key: str, expiry: str = "") -> Dict[str, object]:
    """拉链时的附带信息：标的、现价、到期日、剩余天数、来源描述。"""
    meta = MARKETS.get(market_key, {})
    seasons = list_expiries(market_key)
    if not expiry and seasons:
        expiry = _default_expiry(market_key, seasons)
    exp, days, _ = expiry_info(market_key, expiry) if expiry else (None, 0, 0.0)
    forward = None
    try:
        forward = fetch_option_chain(market_key, expiry).attrs.get("forward")
    except Exception:
        forward = None
    return {
        "market": meta.get("name", market_key),
        "kind": "中金所股指期权" if meta.get("kind") == "cffex" else "上交所 ETF 期权",
        "underlying": meta.get("spot_name", ""),
        "spot": spot_price(market_key),
        "forward": forward,
        "expiry": expiry,
        "expiry_date": exp.isoformat() if exp else "",
        "days_left": days,
        "expiries": seasons,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "新浪财经（sina）",
    }
