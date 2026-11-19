"""
DeepSeek AI 助手接入模块（OpenAI 兼容接口，直连 requests，无需 openai SDK）。

能力：
- 把当前股票多因子打分表、最新新闻整理成上下文；
- 调用 DeepSeek 聊天接口做个股 / 组合解读、新闻总结、自由问答；
- 鉴权缺失、网络错误、接口报错都转为友好提示，不抛出异常。

安全说明：
- API Key 只从本地配置或界面输入读取，不写入日志、不随代码上传。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

SYSTEM_PROMPT = (
    "你是一个严谨的金融信息助手，基于用户提供的股票多因子数据与新闻做客观解读。"
    "回答简洁、分点清晰；明确说明数据可能滞后，所有内容仅供参考，不构成投资建议。"
)


# ---------------------------------------------------------------------------
# 上下文构造
# ---------------------------------------------------------------------------

def _fmt_num(val, digits: int = 2) -> str:
    if val is None or pd.isna(val):
        return "—"
    return f"{float(val):.{digits}f}"


def build_stock_context(df: pd.DataFrame, top: int = 15) -> str:
    """把打分结果整理为简明文本上下文。"""
    if df is None or df.empty:
        return "（当前没有股票数据）"

    lines = ["【当前股票多因子打分表（按综合得分排序）】"]
    for _, row in df.head(top).iterrows():
        lines.append(
            f"- 排名 {row.get('rank', '—')}：{row.get('name', '—')}（{row.get('code', '—')}）"
            f"｜综合分 {_fmt_num(row.get('total_score'), 1)}｜评级 {row.get('grade', '—')}"
            f"｜估值 {_fmt_num(row.get('valuation_score'), 1)}｜动量 {_fmt_num(row.get('momentum_score'), 1)}"
            f"｜质量 {_fmt_num(row.get('quality_score'), 1)}｜波动 {_fmt_num(row.get('volatility_score'), 1)}"
            f"｜流动性 {_fmt_num(row.get('liquidity_score'), 1)}"
            f"｜PE {_fmt_num(row.get('pe'))}｜PB {_fmt_num(row.get('pb'))}"
            f"｜ROE {_fmt_num(row.get('roe'), 1)}｜20日涨跌 {_fmt_num(row.get('momentum_20d'), 1)}%"
        )
    return "\n".join(lines)


def build_news_context(df: pd.DataFrame, top: int = 10) -> str:
    """把最新新闻整理为简明文本上下文。"""
    if df is None or df.empty:
        return "（当前没有新闻数据）"
    lines = ["【最新财经快讯】"]
    for _, row in df.head(top).iterrows():
        title = str(row.get("title", "") or "").strip()
        source = str(row.get("source", "") or "").strip()
        time = str(row.get("time", "") or "").strip()
        lines.append(f"- [{time}] {source}：{title}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 接口调用
# ---------------------------------------------------------------------------

def _post_chat(payload: Dict, api_key: str, base_url: str, timeout: int) -> Tuple[Optional[str], Optional[str]]:
    """调用 /chat/completions，返回 (文本, 错误信息)。成功时错误为 None。"""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        return None, f"网络请求失败：{exc}"

    if resp.status_code != 200:
        try:
            err = resp.json().get("error", {})
            msg = err.get("message") if isinstance(err, dict) else str(err)
        except Exception:
            msg = resp.text[:200]
        return None, f"接口返回 {resp.status_code}：{msg or '未知错误'}"

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return (content or "").strip(), None
    except (KeyError, IndexError, ValueError) as exc:
        return None, f"解析响应失败：{exc}"


def chat(messages: List[Dict[str, str]], cfg: Dict,
         api_key: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """
    通用对话调用。

    参数：
        messages  OpenAI 风格消息列表 [{"role", "content"}]
        cfg       deepseek 配置段
        api_key   覆盖配置中的 Key（界面输入优先）
    返回：(回复文本, 错误信息)
    """
    key = (api_key or cfg.get("api_key") or "").strip()
    if not key:
        return "", "未填写 DeepSeek API Key，请在 AI 助手页填入后再试。"

    payload = {
        "model": cfg.get("model", "deepseek-flash"),
        "messages": messages,
        "temperature": cfg.get("temperature", 0.3),
        "max_tokens": cfg.get("max_tokens", 1024),
        "stream": False,
    }
    return _post_chat(
        payload, key,
        base_url=cfg.get("base_url", "https://api.deepseek.com"),
        timeout=int(cfg.get("timeout", 60)),
    )


def _compose(stock_df: pd.DataFrame, news_df: pd.DataFrame,
             user_question: str, cfg: Dict,
             api_key: Optional[str] = None) -> Tuple[str, Optional[str]]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"{build_stock_context(stock_df)}\n\n"
            f"{build_news_context(news_df)}\n\n"
            f"请基于以上数据回答：{user_question}"
        )},
    ]
    return chat(messages, cfg, api_key=api_key)


def analyze_stocks(stock_df: pd.DataFrame, news_df: pd.DataFrame,
                   cfg: Dict, api_key: Optional[str] = None,
                   question: str = "请简要分析当前打分靠前的标的，指出各自的优势与风险。") -> Tuple[str, Optional[str]]:
    """分析当前股票组合。"""
    return _compose(stock_df, news_df, question, cfg, api_key)


def summarize_news(news_df: pd.DataFrame, cfg: Dict,
                   api_key: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """总结最新新闻要点。"""
    return _compose(
        pd.DataFrame(), news_df,
        "请总结这些最新财经快讯的要点，分点列出，并提示可能影响的板块。",
        cfg, api_key,
    )
