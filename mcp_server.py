"""
MCP Server —— 把监控面板的数据暴露给 DeepSeek Harness（dsh）等 MCP 客户端。

dsh 通过 MCP 协议连接本服务后，其中的 Agent 可以自动调用下列工具查看：
- list_stock_scores   当前股票多因子打分排名
- get_stock_detail    单只个股完整明细
- list_latest_news    最新全球财经快讯
- list_watchlist      自选股票清单

运行方式（被 dsh 以 stdio 方式拉起，一般无需手动执行）：
    python mcp_server.py

本地自测：
    python mcp_server.py
然后用任意 MCP 客户端（或 dsh 的 MCP 探测）连接即可。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

# 确保项目根目录在模块搜索路径中（stdio 子进程场景）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

from src import crypto, data_source, factors, news, options

mcp = FastMCP("monitor-panel")


def _currency(market: str) -> str:
    """当前市场的报价货币，用于在输出里把单位标清楚。"""
    name = str(market or "").strip()
    if name in ("加密", "加密货币"):
        return "USDT"
    if name == "美股":
        return "USD"
    return "元"


def _wants_cny(market: str) -> bool:
    return str(market or "").strip() in ("美股", "加密", "加密货币")

# 进程内缓存，避免每次工具调用重复拉取行情
_cache: dict = {"stocks": None, "cfg": None, "news": None}


def _config() -> dict:
    if _cache["cfg"] is None:
        _cache["cfg"] = data_source.load_config()
    return _cache["cfg"]


def _stock_frame(market: str = "A股"):
    cfg = _config()
    raw = data_source.get_universe(
        market=market,
        source="auto",
        watchlist=cfg.get("stocks", []),
        us_watchlist=cfg.get("us_stocks", []),
        pool_size=cfg.get("settings", {}).get("pool_size"),
        crypto_list=cfg.get("crypto", ["BTC"]),
    )
    return factors.compute_factor_scores(raw, cfg.get("weights", {}))


def _fmt(val, digits: int = 2) -> str:
    if val is None or str(val).strip() in ("nan", "<NA>", "NaT", "None", ""):
        return "—"
    try:
        return f"{float(val):.{digits}f}"
    except (TypeError, ValueError):
        return str(val)


@mcp.tool()
def list_stock_scores(market: str = "A股") -> str:
    """返回当前股票多因子打分排名表：综合分、评级、各因子分与估值指标。

    Args:
        market: 市场，"A股" 或 "美股"。
    """
    df = _stock_frame(market)
    if df.empty:
        return "当前没有股票数据。"

    cur = _currency(market)
    wants_cny = _wants_cny(market)
    rate = crypto.usdcny_rate() if wants_cny else None
    head = ["排名", "代码", "名称", f"最新价({cur})"]
    if wants_cny:
        head.append("折人民币¥")
    head += ["涨跌幅%", "综合分", "评级", "估值", "动量", "质量", "波动", "流动性",
             "PE", "PB", "ROE%", "20日涨跌%"]
    lines = [" | ".join(head)]
    for _, r in df.iterrows():
        cells = [str(r.get("rank", "—")), str(r.get("code", "—")), str(r.get("name", "—")),
                 _fmt(r.get("price"))]
        if wants_cny:
            cny = crypto.to_cny(r.get("price"), rate)
            cells.append("—" if cny != cny else f"{cny:,.0f}")
        cells += [
            _fmt(r.get("change_pct")),
            _fmt(r.get("total_score"), 1), str(r.get("grade", "—")),
            _fmt(r.get("valuation_score"), 1), _fmt(r.get("momentum_score"), 1),
            _fmt(r.get("quality_score"), 1), _fmt(r.get("volatility_score"), 1),
            _fmt(r.get("liquidity_score"), 1),
            _fmt(r.get("pe")), _fmt(r.get("pb")), _fmt(r.get("roe"), 1),
            _fmt(r.get("momentum_20d"), 1),
        ]
        lines.append(" | ".join(cells))
    if wants_cny and rate:
        lines.append(f"\n注：最新价以 {cur} 计价，1 美元 ≈ {rate:.4f} 元人民币。")
    return "\n".join(lines)


@mcp.tool()
def get_stock_detail(code: str, market: str = "A股") -> str:
    """按股票代码或名称关键字查询单只标的的完整因子与行情明细。

    Args:
        code: 代码或名称片段（如 "600519" / "茅台" / "BTC"）。
        market: 市场，"A股" / "美股" / "加密货币"，默认 A股。
    """
    df = _stock_frame(market)
    df = pd_like_filter(df, code)
    if df.empty:
        return f"未找到与「{code}」匹配的标的（市场：{market}）。"

    out = []
    cur = _currency(market)
    rate = crypto.usdcny_rate() if _wants_cny(market) else None
    for _, r in df.iterrows():
        price_line = f"最新价：{_fmt(r.get('price'))} {cur}"
        if rate:
            cny = crypto.to_cny(r.get("price"), rate)
            if cny == cny:
                price_line += f"（≈ ¥{cny:,.0f}，1 美元 ≈ {rate:.4f} 元）"
        out.append(
            f"【{r.get('name', '—')}（{r.get('code', '—')}）】\n"
            f"{price_line}　涨跌幅：{_fmt(r.get('change_pct'))}%\n"
            f"综合得分：{_fmt(r.get('total_score'), 1)}　评级：{r.get('grade', '—')}\n"
            f"因子分 — 估值：{_fmt(r.get('valuation_score'), 1)}　动量：{_fmt(r.get('momentum_score'), 1)}　"
            f"质量：{_fmt(r.get('quality_score'), 1)}　波动：{_fmt(r.get('volatility_score'), 1)}　"
            f"流动性：{_fmt(r.get('liquidity_score'), 1)}\n"
            f"估值指标 — PE：{_fmt(r.get('pe'))}　PB：{_fmt(r.get('pb'))}　ROE：{_fmt(r.get('roe'), 1)}%\n"
            f"动量：近20日涨跌 {_fmt(r.get('momentum_20d'), 1)}%　波动率：{_fmt(r.get('volatility_20d'))}\n"
            f"换手：{_fmt(r.get('turnover'))}%　成交额：{_fmt(r.get('amount'))}　量比：{_fmt(r.get('volume_ratio'))}"
        )
    return "\n\n".join(out)


def pd_like_filter(df, keyword: str):
    """按代码或名称模糊匹配（避免直接依赖 pandas 字符串方法名差异）。"""
    kw = keyword.strip().lower()
    mask = df["code"].astype(str).str.lower().str.contains(kw) | \
        df["name"].astype(str).str.lower().str.contains(kw)
    return df[mask]


@mcp.tool()
def list_latest_news(limit: int = 15) -> str:
    """返回最新全球财经快讯：时间、来源、标题、摘要与原文链接。

    Args:
        limit: 返回条数，默认 15。
    """
    cfg = _config()
    news_cfg = cfg.get("news", {}) or {}
    df = news.fetch_news(
        source_keys=news_cfg.get("sources", ["eastmoney", "cls"]),
        rss_feeds=news_cfg.get("rss_feeds", []) or [],
        limit=int(limit),
    )
    if df.empty:
        return "当前没有新闻数据。"

    lines = []
    for _, r in df.iterrows():
        url = str(r.get("url", "") or "")
        link = f"　链接：{url}" if url else ""
        lines.append(
            f"[{r.get('time', '')}] {r.get('source', '')}：{r.get('title', '')}\n"
            f"摘要：{r.get('summary', '')}{link}"
        )
    return "\n\n".join(lines)


@mcp.tool()
def list_option_markets() -> str:
    """列出可查看的期权品种及其可用到期合约。

    返回中金所股指期权（IO/HO/MO）与上交所 ETF 期权（510050/510300/510500/588000）
    的品种 key、名称、可用到期月份/合约，供 list_option_chain 使用。
    """
    lines = ["品种key | 名称 | 可用到期"]
    for m in options.list_markets():
        try:
            seasons = options.list_expiries(m["key"])
        except Exception:
            seasons = []
        lines.append(f"{m['key']} | {m['name']} | {', '.join(seasons) if seasons else '—'}")
    return "\n".join(lines)


def _resolve_market(keyword: str) -> str:
    """把 key 或中文名称片段解析为市场 key。"""
    kw = (keyword or "io").strip()
    if kw in options.MARKETS:
        return kw
    low = kw.lower()
    for key, meta in options.MARKETS.items():
        if low and (low in meta["name"].lower() or low == key):
            return key
    for key, meta in options.MARKETS.items():
        if key in low or meta.get("prefix", "\0") in low:
            return key
    return "io"


@mcp.tool()
def list_option_chain(market: str = "io", expiry: str = "", max_rows: int = 40) -> str:
    """返回真实期权链行情：真实买卖价、持仓量、反解隐含波动率与 Delta。

    Args:
        market: 品种，key（io/ho/mo/510050/510300/510500/588000）或名称片段。
        expiry: 到期合约/月份，如 "io2612" 或 "202612"；空则自动取最近有效月。
        max_rows: 最多返回多少档行权价（按与现价接近程度排序），默认 40。
    """
    key = _resolve_market(market)
    try:
        chain = options.fetch_option_chain(market_key=key, expiry=expiry)
        info = options.chain_summary(market_key=key, expiry=expiry)
    except Exception as exc:
        return f"期权行情拉取失败：{exc}"
    if chain.empty:
        return f"{options.MARKETS[key]['name']} 该合约暂无行情数据。"

    spot = info.get("spot")
    if spot and "行权价" in chain.columns:
        chain = chain.reindex(
            (chain["行权价"] - float(spot)).abs().sort_values().index
        ).sort_values("行权价")
    chain = chain.head(int(max_rows))

    head = (
        f"{info['market']}｜合约 {info['expiry']}｜到期 {info['expiry_date']}"
        f"（剩 {info['days_left']} 天）｜标的现价 {_fmt(spot)}｜来源 {info['source']}"
        f"｜抓取 {info['fetched_at']}"
    )
    lines = [head, "",
             "行权价 | 虚实值 | Call最新 | Call买 | Call卖 | Call持仓 | Call IV | Call Δ"
             " | Put最新 | Put买 | Put卖 | Put持仓 | Put IV | Put Δ"]
    for _, r in chain.iterrows():
        iv_c = r.get("Call隐含波动率")
        iv_p = r.get("Put隐含波动率")
        lines.append(" | ".join([
            _fmt(r.get("行权价"), 1), str(r.get("虚实值", "—")),
            _fmt(r.get("Call最新价"), 4), _fmt(r.get("Call买价"), 4), _fmt(r.get("Call卖价"), 4),
            _fmt(r.get("Call持仓量"), 0),
            _fmt(iv_c * 100, 1) if iv_c == iv_c and iv_c is not None else "—",
            _fmt(r.get("Call Delta"), 3),
            _fmt(r.get("Put最新价"), 4), _fmt(r.get("Put买价"), 4), _fmt(r.get("Put卖价"), 4),
            _fmt(r.get("Put持仓量"), 0),
            _fmt(iv_p * 100, 1) if iv_p == iv_p and iv_p is not None else "—",
            _fmt(r.get("Put Delta"), 3),
        ]))
    return "\n".join(lines)


@mcp.tool()
def list_watchlist() -> str:
    """返回配置的 A 股与美股自选清单。"""
    cfg = _config()
    lines = ["【A 股自选】"]
    for item in cfg.get("stocks", []):
        lines.append(f"- {item['code']}　{item.get('name', '')}")
    lines.append("\n【美股自选】")
    for item in cfg.get("us_stocks", []):
        lines.append(f"- {item['code']}　{item.get('name', '')}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
