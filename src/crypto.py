"""加密货币行情（比特币等）。

数据源：Binance 公开数据域名 ``data-api.binance.vision``（境内可直连，
主站 api.binance.com 与 OKX / 火币 / Coinbase 均不通）。
所有接口无需 key，单次请求 1~3s，结果做 20 秒缓存。
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Dict, List, Optional, Sequence

import pandas as pd

BASE = "https://data-api.binance.vision"
CACHE_TTL = 20

# 面板里支持的币种：代码 -> (中文名, 交易所交易对)
COINS: Dict[str, tuple] = {
    "BTC": ("比特币", "BTCUSDT"),
    "ETH": ("以太坊", "ETHUSDT"),
    "SOL": ("索拉纳", "SOLUSDT"),
    "BNB": ("币安币", "BNBUSDT"),
    "XRP": ("瑞波币", "XRPUSDT"),
    "DOGE": ("狗狗币", "DOGEUSDT"),
    "ADA": ("艾达币", "ADAUSDT"),
    "LTC": ("莱特币", "LTCUSDT"),
}

COLUMNS = ["time", "open", "high", "low", "close", "volume", "amount"]
_CACHE: Dict[str, tuple] = {}

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _cached(key: str):
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    return None


def _store(key: str, value):
    _CACHE[key] = (time.time(), value)
    return value


def clear_cache() -> None:
    _CACHE.clear()


def _get_json(path: str, params: Optional[dict] = None, timeout: int = 20):
    query = ""
    if params:
        from urllib.parse import urlencode

        query = "?" + urlencode(params)
    req = urllib.request.Request(BASE + path + query, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


SINA_FX_URL = "https://hq.sinajs.cn/list=fx_susdcny"
FX_TTL = 60
_FX_HEADERS = dict(_HEADERS)
_FX_HEADERS["Referer"] = "https://finance.sina.com.cn"


def usdcny_rate(force: bool = False) -> Optional[float]:
    """美元兑人民币（新浪 fx_susdcny 在岸即期）。

    新浪接口返回 GBK，字段：0时间 1买价 2卖价 3昨收 5今开 6最高 7最低 8现价。
    取不到时返回 None，界面上显示「—」而不是假装有个汇率。
    """
    if not force:
        hit = _CACHE.get("usdcny")
        if hit and time.time() - hit[0] < FX_TTL:
            return hit[1]
    rate = None
    try:
        req = urllib.request.Request(SINA_FX_URL, headers=_FX_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("gbk", "ignore")
        body = raw.split('"')[1] if '"' in raw else ""
        parts = body.split(",")
        if len(parts) > 8:
            value = float(parts[8])
            if 0 < value < 100:
                rate = value
    except Exception:
        rate = None
    _CACHE["usdcny"] = (time.time(), rate)
    return rate


def to_cny(price, rate: Optional[float] = None):
    """把 USDT/USD 价格折成人民币；汇率缺失或价格缺失时返回 NaN。"""
    if price is None or rate is None:
        return float("nan")
    try:
        value = float(price)
        ratio = float(rate)
    except (TypeError, ValueError):
        return float("nan")
    if value != value or ratio != ratio:
        return float("nan")
    return value * ratio


def coin_name(code: str) -> str:
    raw = str(code).strip().upper()
    return COINS.get(raw, (raw, raw + "USDT"))[0]


def pair_of(code: str) -> str:
    """BTC / BTCUSDT / btcusdt 都能归一成 BTCUSDT。"""
    raw = str(code).strip().upper()
    if raw in COINS:
        return COINS[raw][1]
    if raw.endswith("USDT"):
        return raw
    return raw + "USDT"


def fetch_kline(code: str, interval: str = "5m", limit: int = 48) -> pd.DataFrame:
    """取 K 线。interval: 1m/5m/15m/1h/4h/1d。"""
    pair = pair_of(code)
    key = f"k:{pair}:{interval}:{limit}"
    hit = _cached(key)
    if hit is not None:
        return hit.copy()
    rows = _get_json("/api/v3/klines",
                     {"symbol": pair, "interval": interval, "limit": int(limit)})
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.DataFrame([{
        # 交易所返回 UTC 毫秒时间戳，统一换成本地（北京时间）去掉时区
        "time": pd.to_datetime(r[0], unit="ms", utc=True)
                .tz_convert("Asia/Shanghai").tz_localize(None),
        "open": float(r[1]),
        "high": float(r[2]),
        "low": float(r[3]),
        "close": float(r[4]),
        "volume": float(r[5]),
        "amount": float(r[7]),  # quoteVolume，USDT 计价成交额
    } for r in rows])
    frame = frame.sort_values("time").reset_index(drop=True)
    return _store(key, frame).copy()


def fetch_tickers(codes: Sequence[str]) -> pd.DataFrame:
    """24 小时快照：现价、涨跌幅、最高、最低、成交额。"""
    pairs = [pair_of(c) for c in codes]
    out: List[dict] = []
    for pair in pairs:
        key = f"t:{pair}"
        hit = _cached(key)
        if hit is not None:
            out.append(dict(hit))
            continue
        try:
            j = _get_json("/api/v3/ticker/24hr", {"symbol": pair})
        except Exception:
            continue
        if not isinstance(j, dict) or "lastPrice" not in j:
            continue
        row = {
            "pair": pair,
            "code": pair.replace("USDT", ""),
            "name": coin_name(pair.replace("USDT", "")),
            "price": float(j["lastPrice"]),
            "change_pct": float(j.get("priceChangePercent") or 0.0),
            "high_24h": float(j.get("highPrice") or 0.0),
            "low_24h": float(j.get("lowPrice") or 0.0),
            "volume_24h": float(j.get("volume") or 0.0),
            "amount_24h": float(j.get("quoteVolume") or 0.0),
        }
        _store(key, row)
        out.append(dict(row))
    return pd.DataFrame(out)


def _daily_stats(code: str, window: int = 20) -> Dict[str, Optional[float]]:
    """用日线算 N 日动量与日均波动率（%）。"""
    try:
        daily = fetch_kline(code, "1d", max(int(window) + 2, 22))
    except Exception:
        return {"momentum_20d": None, "volatility_20d": None}
    if daily.empty or len(daily) < 3:
        return {"momentum_20d": None, "volatility_20d": None}
    closes = daily["close"].astype(float)
    n = min(int(window), len(closes) - 1)
    momentum = (closes.iloc[-1] / closes.iloc[-1 - n] - 1.0) * 100 if n > 0 else None
    rets = closes.pct_change().dropna().tail(max(n, 5)) * 100.0
    volatility = float(rets.std()) if not rets.empty else None
    return {
        "momentum_20d": None if momentum is None else round(float(momentum), 2),
        "volatility_20d": None if volatility is None else round(volatility, 2),
    }


def fetch_universe(codes: Sequence[str], window: int = 20) -> pd.DataFrame:
    """返回与 A股/美股 同构的 DataFrame，供因子打分复用。"""
    tickers = fetch_tickers(codes)
    if tickers.empty:
        return pd.DataFrame()
    rows = []
    for _, t in tickers.iterrows():
        stats = _daily_stats(t["code"], window)
        rows.append({
            "code": t["code"],
            "name": t["name"],
            "price": t["price"],
            "change_pct": t["change_pct"],
            # 加密货币没有这些财务口径，用 NaN 保持与 A股/美股 相同的 dtype
            "pe": float("nan"),
            "pb": float("nan"),
            "roe": float("nan"),
            "momentum_20d": stats["momentum_20d"],
            "volatility_20d": stats["volatility_20d"],
            "turnover": float("nan"),
            "amount": t["amount_24h"] / 1e8,  # 亿 USDT，与 A 股成交额量级对齐
            "volume_ratio": float("nan"),
            "high_24h": t["high_24h"],
            "low_24h": t["low_24h"],
        })
    frame = pd.DataFrame(rows)
    for col in ("momentum_20d", "volatility_20d"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    # 人民币折算列：币安是 USDT 计价，界面上同时给一列 ¥ 方便对账
    rate = usdcny_rate()
    if rate:
        frame["price_cny"] = [
            to_cny(p, rate) for p in frame["price"]
        ]
        frame["fx_rate"] = rate
    else:
        frame["price_cny"] = float("nan")
        frame["fx_rate"] = float("nan")
    frame.attrs["source"] = "crypto"
    frame.attrs["currency"] = "USDT"
    frame.attrs["fx_rate"] = rate
    return frame


def last_price(code: str) -> Optional[float]:
    frame = fetch_kline(code, "1m", 1)
    if frame.empty:
        return None
    return float(frame["close"].iloc[-1])
