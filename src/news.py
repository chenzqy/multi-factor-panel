"""
全球新闻聚合模块。

数据源：
- 东方财富全球财经快讯（标题 / 摘要 / 时间 / 链接齐全）
- 财联社电报（标题 / 内容 / 时间，无链接）
- 可选境外 RSS 源（feedparser 解析，网络不通自动跳过）

所有数据源单独容错：某一源失败不影响其他源；全部失败时返回内置示例新闻。

标准输出列：
    time     发布时间字符串（YYYY-MM-DD HH:MM:SS）
    source   来源名称
    title    标题
    summary  摘要
    url      原文链接（可能为空）
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

NEWS_COLUMNS = ["time", "source", "title", "summary", "url"]

_SUMMARY_MAX = 120


def _clean(text: Optional[str]) -> str:
    """去除 HTML 标签、转义字符与多余空白。"""
    if text is None:
        return ""
    text = re.sub(r"<[^>]+>", "", str(text))
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _cut(text: str, limit: int = _SUMMARY_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_dt(value) -> Optional[datetime]:
    """尽力解析时间为 datetime；失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "year") and hasattr(value, "hour"):  # date / time 组合
        try:
            if isinstance(value, datetime):
                return value
            from datetime import date as _date, time as _time
            if isinstance(value, _date):
                return datetime(value.year, value.month, value.day)
            if isinstance(value, _time):
                return datetime(2000, 1, 1, value.hour, value.minute, value.second)
        except Exception:
            return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
                "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# 国内全球财经快讯（akshare）
# ---------------------------------------------------------------------------

def fetch_eastmoney() -> pd.DataFrame:
    """东方财富全球财经快讯。"""
    import akshare as ak
    df = ak.stock_news_global_em()
    out = pd.DataFrame({
        "time": df["发布时间"].astype(str),
        "source": "东方财富",
        "title": df["标题"].map(_clean),
        "summary": df["摘要"].map(_clean).map(_cut),
        "url": df["链接"].astype(str),
    })
    return out[NEWS_COLUMNS]


def fetch_cls() -> pd.DataFrame:
    """财联社电报（无原文链接）。"""
    import akshare as ak
    df = ak.stock_news_global_cls(symbol="全部")
    # 发布日期(date) + 发布时间(time) 拼接
    time_str = []
    for _, row in df.iterrows():
        d = row.get("发布日期")
        t = row.get("发布时间")
        try:
            time_str.append(f"{d} {t}")
        except Exception:
            time_str.append("")
    out = pd.DataFrame({
        "time": time_str,
        "source": "财联社",
        "title": df["标题"].map(_clean),
        "summary": df["内容"].map(_clean).map(_cut),
        "url": "",
    })
    return out[NEWS_COLUMNS]


_SOURCE_FUNCS = {
    "eastmoney": fetch_eastmoney,
    "cls": fetch_cls,
}


# ---------------------------------------------------------------------------
# RSS 源（可选）
# ---------------------------------------------------------------------------

def fetch_rss(feeds: List[Dict[str, str]]) -> pd.DataFrame:
    """解析境外 RSS 源；单个源失败自动跳过。"""
    import feedparser

    rows: List[Dict[str, str]] = []
    for feed in feeds:
        name = feed.get("name", "RSS")
        url = feed.get("url", "")
        if not url:
            continue
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:30]:
                title = _clean(getattr(entry, "title", ""))
                summary = _cut(_clean(getattr(entry, "summary", "")))
                link = getattr(entry, "link", "") or ""
                dt = (getattr(entry, "published_parsed", None)
                      or getattr(entry, "updated_parsed", None))
                time_str = ""
                if dt:
                    try:
                        time_str = datetime(*dt[:6]).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        time_str = ""
                if title:
                    rows.append({
                        "time": time_str, "source": name,
                        "title": title, "summary": summary, "url": link,
                    })
        except Exception:
            continue
    return pd.DataFrame(rows, columns=NEWS_COLUMNS)


# ---------------------------------------------------------------------------
# 聚合入口
# ---------------------------------------------------------------------------

def _sample_news() -> pd.DataFrame:
    """离线兜底示例新闻。"""
    return pd.DataFrame([
        {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "source": "示例", "title": "当前网络不可用，以下为演示新闻",
         "summary": "联网后点击“刷新”即可获取全球财经快讯。", "url": ""},
        {"time": "", "source": "示例", "title": "全球市场动态将在此聚合展示",
         "summary": "支持东方财富、财联社及自定义 RSS 源。", "url": ""},
    ])


def fetch_news(source_keys: List[str], rss_feeds: Optional[List[Dict[str, str]]] = None,
               limit: int = 50) -> pd.DataFrame:
    """
    聚合多源新闻，按发布时间倒序。

    参数：
        source_keys  国内快讯源标识：eastmoney / cls
        rss_feeds    可选 RSS 源列表 [{name, url}]
        limit        最大返回条数
    """
    frames: List[pd.DataFrame] = []

    for key in source_keys or []:
        func = _SOURCE_FUNCS.get(key)
        if func is None:
            continue
        try:
            frames.append(func())
        except Exception:
            continue

    if rss_feeds:
        try:
            frames.append(fetch_rss(rss_feeds))
        except Exception:
            pass

    if not frames:
        return _sample_news()

    news = pd.concat(frames, ignore_index=True)
    # 去重（按标题）
    news = news.drop_duplicates(subset=["title"], keep="first")
    # 按可解析时间倒序；解析失败的排最后
    news["_dt"] = news["time"].map(_parse_dt)
    news = news.sort_values("_dt", ascending=False, na_position="last")
    news = news.drop(columns=["_dt"]).head(limit).reset_index(drop=True)
    return news[NEWS_COLUMNS]
