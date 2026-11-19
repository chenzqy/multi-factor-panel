"""
多因子计算与打分模块。

因子清单（每个因子最终输出 0~100 的截面得分，分数越高越符合偏好）：
    valuation   估值：PE、PB 越低越好（亏损股 PE<=0 记为最差）
    momentum    动量：近 N 日涨幅越高越好
    quality     质量：ROE 越高越好
    volatility  波动：波动率越低越稳健越好
    liquidity   流动性：成交额越活跃越好

打分口径：在当前股票池内做横截面百分位排名（0~100），
综合得分 = 各因子得分按权重加权（权重自动归一化）。
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

# 因子方向：high 表示原始值越大越好，low 表示越小越好
FACTOR_DIRECTION = {
    "valuation": "low",
    "momentum": "high",
    "quality": "high",
    "volatility": "low",
    "liquidity": "high",
}

FACTOR_LABELS = {
    "valuation": "估值",
    "momentum": "动量",
    "quality": "质量",
    "volatility": "波动(稳健)",
    "liquidity": "流动性",
}


def _percentile_score(series: pd.Series, direction: str) -> pd.Series:
    """横截面百分位排名，输出 0~100；缺失值用中位数 50 分填充。"""
    s = pd.to_numeric(series, errors="coerce")
    # 单个标的（或只有一个有效值）时排名没有横截面意义，统一给中性 50 分，
    # 否则 rank(pct=True) 会恒等于 1.0，导致"越低越好"的因子恒得 0 分
    if s.notna().sum() <= 1:
        return pd.Series(50.0, index=series.index)
    rank = s.rank(pct=True, method="average") * 100
    if direction == "low":
        rank = 100 - rank
    # 缺失值给中性 50 分
    rank = rank.fillna(50.0)
    return rank.round(1)


def _build_raw_factors(df: pd.DataFrame) -> pd.DataFrame:
    """从原始行情/财务列构造各因子的原始值。"""
    out = pd.DataFrame(index=df.index)

    # 估值：PE、PB 等权合成；亏损(PE<=0)估值记为最差
    pe = df["pe"].where(df["pe"] > 0, np.nan)
    pe_score = pe.rank(pct=True)
    pb_score = df["pb"].rank(pct=True)
    out["valuation"] = ((pe_score + pb_score) / 2) * 100
    # 仅对真实存在且非正的 PE（亏损股）记最差；缺失值保持中性，不计为亏损
    loss_mask = df["pe"].notna() & (df["pe"] <= 0)
    out.loc[loss_mask, "valuation"] = 0.0

    out["momentum"] = df["momentum_20d"]
    out["quality"] = df["roe"]
    out["volatility"] = df["volatility_20d"]
    out["liquidity"] = df["amount"]
    return out


def _grade(score: float) -> str:
    if score >= 80:
        return "强势"
    if score >= 60:
        return "偏强"
    if score >= 40:
        return "中性"
    return "偏弱"


def compute_factor_scores(df: pd.DataFrame, weights: Dict[str, float]) -> pd.DataFrame:
    """
    计算因子得分与综合排名。

    返回：在原 df 基础上追加
        <factor>_score  各因子 0~100 得分
        total_score     加权综合得分
        grade           评级
        rank            综合排名（1 为最优）
    """
    result = df.copy()
    raw = _build_raw_factors(df)

    factor_scores = {}
    for factor, direction in FACTOR_DIRECTION.items():
        factor_scores[f"{factor}_score"] = _percentile_score(raw[factor], direction)

    score_df = pd.DataFrame(factor_scores, index=df.index)

    # 权重归一化
    w = {k: float(v) for k, v in weights.items() if k in FACTOR_DIRECTION}
    total_w = sum(w.values()) or 1.0
    norm_w = {k: v / total_w for k, v in w.items()}

    result["total_score"] = 0.0
    for factor, weight in norm_w.items():
        result["total_score"] += score_df[f"{factor}_score"] * weight
    result["total_score"] = result["total_score"].round(1)

    result = pd.concat([result, score_df], axis=1)
    result.attrs.update(df.attrs)  # 保留来源标记等元信息
    result["grade"] = result["total_score"].apply(_grade)
    result = result.sort_values("total_score", ascending=False).reset_index(drop=True)
    result["rank"] = result.index + 1
    return result
