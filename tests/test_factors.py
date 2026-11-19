"""因子打分逻辑的单元测试（纯 pandas，不依赖任何网络或 Qt）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.factors import FACTOR_DIRECTION, compute_factor_scores

WEIGHTS = {
    "valuation": 0.25,
    "momentum": 0.20,
    "quality": 0.25,
    "volatility": 0.15,
    "liquidity": 0.15,
}


def make_frame() -> pd.DataFrame:
    """三只股票：一只便宜且强、一只一般、一只亏损且弱。"""
    return pd.DataFrame(
        {
            "code": ["000001", "000002", "000003"],
            "name": ["便宜强势", "平均值", "亏损弱势"],
            "price": [10.0, 20.0, 5.0],
            "change_pct": [1.0, 0.0, -2.0],
            "pe": [8.0, 20.0, -3.0],
            "pb": [0.9, 2.5, 4.0],
            "roe": [18.0, 10.0, -5.0],
            "turnover": [1.0, 2.0, 3.0],
            "volume_ratio": [1.0, 1.0, 1.0],
            "momentum_20d": [12.0, 3.0, -8.0],
            "volatility_20d": [1.5, 2.5, 4.0],
            "amount": [30.0, 12.0, 2.0],
        }
    )


def test_outputs_all_columns():
    scored = compute_factor_scores(make_frame(), WEIGHTS)
    for factor in FACTOR_DIRECTION:
        assert f"{factor}_score" in scored.columns
    for col in ("total_score", "grade", "rank"):
        assert col in scored.columns


def test_rank_is_dense_and_ordered():
    scored = compute_factor_scores(make_frame(), WEIGHTS)
    assert list(scored["rank"]) == [1, 2, 3]
    totals = list(scored["total_score"])
    assert totals == sorted(totals, reverse=True)


def test_good_row_beats_bad_row():
    scored = compute_factor_scores(make_frame(), WEIGHTS).set_index("code")
    assert scored.loc["000001", "total_score"] > scored.loc["000003", "total_score"]
    assert scored.loc["000001", "valuation_score"] > scored.loc["000003", "valuation_score"]


def test_loss_making_pe_gets_worst_valuation():
    scored = compute_factor_scores(make_frame(), WEIGHTS).set_index("code")
    # PE <= 0 的亏损股估值分被钉在 0
    assert scored.loc["000003", "valuation_score"] == 0.0


def test_missing_pe_is_neutral_not_loss():
    """PE 缺失只代表"数据没取到"，不能被当成亏损股打 0 分。"""
    df = make_frame()
    df.loc[1, "pe"] = np.nan
    scored = compute_factor_scores(df, WEIGHTS).set_index("code")
    assert scored.loc["000002", "valuation_score"] > 0.0


def test_single_row_is_neutral():
    """只有一个标的时横截面排名没有意义，所有因子应给中性 50 分。"""
    scored = compute_factor_scores(make_frame().head(1), WEIGHTS)
    for factor in FACTOR_DIRECTION:
        assert scored.loc[0, f"{factor}_score"] == pytest.approx(50.0)
    assert scored.loc[0, "total_score"] == pytest.approx(50.0)
    assert scored.loc[0, "grade"] == "中性"


def test_weights_are_normalized():
    """权重全部乘 10 不应改变综合分（内部会归一化）。"""
    base = compute_factor_scores(make_frame(), WEIGHTS)
    scaled = compute_factor_scores(make_frame(), {k: v * 10 for k, v in WEIGHTS.items()})
    assert list(base["total_score"]) == list(scaled["total_score"])


def test_attrs_are_preserved():
    """数据来源标记要带到结果里，界面靠它显示"实时行情 / 仅代码表"。"""
    df = make_frame()
    df.attrs["source"] = "codes"
    scored = compute_factor_scores(df, WEIGHTS)
    assert scored.attrs.get("source") == "codes"
