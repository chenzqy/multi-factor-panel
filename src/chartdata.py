"""K 线 / 分时数据获取。

A 股统一走新浪公开 JSON 接口（不限流、无需 key）：
    https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData
        ?symbol=sh600519&scale=5&ma=no&datalen=240
    scale=5/15/30/60 为分钟线，scale=240 即日线。

美股新浪该服务已下线（返回 Service not valid），改用 yfinance；失败时抛出
明确异常由界面提示，不影响其它功能。
"""

from __future__ import annotations

import json
import urllib.request
from typing import Optional

import pandas as pd

from src import crypto

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn",
}
_KLINE_URL = ("https://quotes.sina.cn/cn/api/json_v2.php/"
              "CN_MarketDataService.getKLineData")

COLUMNS = ["time", "open", "high", "low", "close", "volume", "amount"]


def sina_symbol(code: str, market: str = "A股") -> str:
    """把 600519 / 300308 这类代码转成 sh600519 / sz300308。"""
    raw = str(code).strip()
    if market == "美股":
        return raw.upper()
    low = raw.lower()
    if low[:2] in ("sh", "sz", "bj"):
        return low
    if raw.startswith("6"):
        return "sh" + raw.zfill(6)
    if raw.startswith(("0", "3")):
        return "sz" + raw.zfill(6)
    if raw.startswith(("4", "8", "9")):
        return "bj" + raw.zfill(6)
    return "sh" + raw.zfill(6)


def _http_get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def _sina_kline(symbol: str, scale: int, datalen: int) -> pd.DataFrame:
    url = f"{_KLINE_URL}?symbol={symbol}&scale={scale}&ma=no&datalen={datalen}"
    text = _http_get(url).strip()
    if not text or text.startswith("null"):
        return pd.DataFrame(columns=COLUMNS)
    try:
        rows = json.loads(text)
    except ValueError:
        return pd.DataFrame(columns=COLUMNS)
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows)
    if "day" not in df:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.DataFrame({
        "time": pd.to_datetime(df["day"], errors="coerce"),
        "open": pd.to_numeric(df.get("open"), errors="coerce"),
        "high": pd.to_numeric(df.get("high"), errors="coerce"),
        "low": pd.to_numeric(df.get("low"), errors="coerce"),
        "close": pd.to_numeric(df.get("close"), errors="coerce"),
        "volume": pd.to_numeric(df.get("volume"), errors="coerce"),
        "amount": pd.to_numeric(df.get("amount"), errors="coerce"),
    })
    out = out.dropna(subset=["time", "close"]).sort_values("time").reset_index(drop=True)
    return out


def _yf_frame(code: str, period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf  # 延迟导入：取不到时才需要

    hist = yf.Ticker(str(code).upper()).history(period=period, interval=interval,
                                                auto_adjust=False)
    if hist is None or hist.empty:
        raise RuntimeError(f"{code} 无行情数据（yfinance 返回空）")
    hist = hist.reset_index()
    time_col = "Datetime" if "Datetime" in hist.columns else "Date"
    out = pd.DataFrame({
        "time": pd.to_datetime(hist[time_col], errors="coerce", utc=True)
                .dt.tz_convert(None),
        "open": pd.to_numeric(hist["Open"], errors="coerce"),
        "high": pd.to_numeric(hist["High"], errors="coerce"),
        "low": pd.to_numeric(hist["Low"], errors="coerce"),
        "close": pd.to_numeric(hist["Close"], errors="coerce"),
        "volume": pd.to_numeric(hist["Volume"], errors="coerce"),
    })
    out["amount"] = out["close"] * out["volume"]
    out = out.dropna(subset=["time", "close"]).sort_values("time").reset_index(drop=True)
    return out[COLUMNS]


def fetch_intraday(code: str, market: str = "A股", scale: int = 5,
                   datalen: int = 240, last_day_only: bool = True) -> pd.DataFrame:
    """取分钟级 K 线（默认 5 分钟）。

    last_day_only=True 时只保留最后一个交易日的分时，避免跨日夜间隔被连成
    一条斜线，也让分时图符合"当日走势"的直觉。
    """
    if market in ("加密", "加密货币"):
        # 7×24 连续交易，不做"当日"裁剪：直接给最近 N 根
        return crypto.fetch_kline(code, f"{scale}m", datalen)
    if market == "美股":
        span = "5d" if scale <= 15 else "1mo"
        frame = _yf_frame(code, span, f"{scale}m")
    else:
        frame = _sina_kline(sina_symbol(code, market), scale, datalen)
    if last_day_only and not frame.empty:
        last_day = frame["time"].dt.date.max()
        frame = frame[frame["time"].dt.date == last_day].reset_index(drop=True)
    return frame


def _sina_us_daily(code: str, days: int = 120) -> pd.DataFrame:
    """美股日线（新浪 US_MinKService，返回全历史，取尾部 days 根）。

    美股分钟线新浪已下线，但日线接口仍可用，且比 yfinance 稳（不受 429 影响）。
    """
    symbol = str(code).strip().upper().replace("-", ".")
    url = ("https://stock.finance.sina.com.cn/usstock/api/json_v2.php/"
           f"US_MinKService.getDailyK?symbol={symbol}&___qn=3")
    text = _http_get(url, timeout=20)
    start = text.find("[")
    if start < 0:
        return pd.DataFrame(columns=COLUMNS)
    try:
        rows = json.loads(text[start:])
    except ValueError:
        return pd.DataFrame(columns=COLUMNS)
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "time": pd.to_datetime(df.get("d"), errors="coerce"),
        "open": pd.to_numeric(df.get("o"), errors="coerce"),
        "high": pd.to_numeric(df.get("h"), errors="coerce"),
        "low": pd.to_numeric(df.get("l"), errors="coerce"),
        "close": pd.to_numeric(df.get("c"), errors="coerce"),
        "volume": pd.to_numeric(df.get("v"), errors="coerce"),
    })
    out["amount"] = out["close"] * out["volume"]
    out = out.dropna(subset=["time", "close"]).sort_values("time").reset_index(drop=True)
    return out.tail(int(days)).reset_index(drop=True)[COLUMNS]


def fetch_daily(code: str, market: str = "A股", days: int = 120) -> pd.DataFrame:
    """取日 K 线。"""
    if market in ("加密", "加密货币"):
        return crypto.fetch_kline(code, "1d", days)
    if market == "美股":
        try:
            frame = _sina_us_daily(code, days)
        except Exception:
            frame = pd.DataFrame(columns=COLUMNS)
        if not frame.empty:
            return frame
        span = "6mo" if days <= 130 else "1y"
        return _yf_frame(code, span, "1d")
    return _sina_kline(sina_symbol(code, market), 240, days)


def last_price(frame: pd.DataFrame) -> Optional[float]:
    if frame is None or frame.empty:
        return None
    value = frame["close"].iloc[-1]
    return None if pd.isna(value) else float(value)
