"""选股筛选封装：在因子打分结果上做条件过滤与排序。"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd


def filter_universe(
    df: pd.DataFrame,
    min_score: float = 0.0,
    grades: Optional[List[str]] = None,
    code_query: str = "",
    sort_by: str = "total_score",
    ascending: bool = False,
) -> pd.DataFrame:
    """
    按条件过滤打分结果。

    参数：
        df          compute_factor_scores 的输出
        min_score   综合得分下限
        grades      保留的评级列表（强势/偏强/中性/偏弱），为空不过滤
        code_query  代码或名称关键字
        sort_by     排序列
        ascending   是否升序
    """
    out = df.copy()

    out = out[out["total_score"] >= min_score]

    if grades:
        out = out[out["grade"].isin(grades)]

    if code_query:
        q = code_query.strip()
        out = out[
            out["code"].str.contains(q, na=False)
            | out["name"].str.contains(q, na=False)
        ]

    if sort_by in out.columns:
        out = out.sort_values(sort_by, ascending=ascending).reset_index(drop=True)
    return out


def to_csv(df: pd.DataFrame) -> str:
    """转成带表头的 CSV 字符串，供面板下载。"""
    return df.to_csv(index=False, encoding="utf-8-sig")
