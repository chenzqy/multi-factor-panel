"""无需 GUI 的最小验证脚本 —— 用来确认「数据层真的能跑」，与界面无关。

它做的事只有三件：
1. 读配置，打印股票池规模；
2. 拉 A 股（或指定市场）行情，跑一次多因子打分；
3. 打印前 5 名与数据来源档位。

不依赖 PySide6，20 秒内出结果。CI、临时排障、给别人演示「它真能跑」都用这个。

用法：
    python -m src.cli                 # 默认 A 股
    python -m src.cli --market 美股
    python -m src.cli --market 加密 --top 3
    python -m src.cli --source codes   # 只看离线代码表，不联网
"""

from __future__ import annotations

import argparse
import sys
import time

from src import data_source, factors

MARKETS = ("A股", "美股", "加密")


def _fmt(val, digits: int = 2) -> str:
    """把 NaN / None / pd.NA 统一显示成 —，避免把"没有数据"伪装成 0。"""
    if val is None:
        return "—"
    try:
        f = float(val)
    except (TypeError, ValueError):
        pass
    else:
        if f != f:  # NaN
            return "—"
        return f"{f:.{digits}f}"
    return "—" if str(val).strip() in ("<NA>", "NaT", "None", "") else str(val)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="多因子面板的最小数据链路验证")
    parser.add_argument("--market", default="A股", choices=MARKETS, help="市场")
    parser.add_argument("--source", default="auto", choices=("auto", "live", "codes"),
                       help="数据来源：auto 走降级链，live 只走实时，codes 只看离线代码表")
    parser.add_argument("--top", type=int, default=5, help="打印前几名")
    args = parser.parse_args(argv)

    cfg = data_source.load_config()
    settings = cfg.get("settings", {}) or {}
    watchlist = cfg.get("stocks", []) if args.market == "A股" else []
    us_watchlist = cfg.get("us_stocks", []) if args.market == "美股" else []

    print(f"市场：{args.market}　数据来源：{args.source}")
    print(f"股票池：A 股 {len(cfg.get('stocks', []))} 只 / 美股 {len(cfg.get('us_stocks', []))} 只"
          f" / 加密 {len(cfg.get('crypto', []) or [])} 个")
    print(f"权重：{cfg.get('weights', {})}")
    print("-" * 72)

    started = time.time()
    raw = data_source.get_universe(
        market=args.market,
        source=args.source,
        watchlist=watchlist,
        us_watchlist=us_watchlist,
        pool_size=settings.get("pool_size"),
        crypto_list=cfg.get("crypto", ["BTC"]),
    )
    elapsed = time.time() - started

    if raw is None or raw.empty:
        print("没拿到任何标的。检查网络，或改用 --source codes 看离线代码表。")
        return 1

    source_tag = raw.attrs.get("source", "unknown")
    scored = factors.compute_factor_scores(raw, cfg.get("weights", {}))

    print(f"耗时 {elapsed:.1f}s　标的 {len(scored)} 只　数据档位：{source_tag}")

    # 缺数列统计：哪一档数据缺了什么，一眼能看出来
    missing = []
    for col, label in (("pe", "PE"), ("pb", "PB"), ("roe", "ROE"),
                       ("momentum_20d", "动量"), ("volatility_20d", "波动")):
        if col in scored.columns:
            n = int(scored[col].isna().sum())
            if n:
                missing.append(f"{label} 缺 {n}/{len(scored)}")
    if missing:
        print("　缺数：" + "，".join(missing))

    print("-" * 72)
    head = f"{'排名':<4}{'代码':<8}{'名称':<10}{'最新价':>10}{'涨跌幅%':>9}{'综合分':>8}{'评级':>6}"
    print(head)
    for _, r in scored.head(max(1, args.top)).iterrows():
        print(f"{int(r['rank']):<4}{str(r['code']):<8}{str(r['name']):<10}"
              f"{_fmt(r.get('price')):>10}{_fmt(r.get('change_pct')):>9}"
              f"{_fmt(r.get('total_score'), 1):>8}{str(r.get('grade', '—')):>6}")

    dist = scored["grade"].value_counts().to_dict() if "grade" in scored.columns else {}
    print("-" * 72)
    print("评级分布：" + "　".join(f"{k} {v}" for k, v in dist.items()))
    print("注意：以上均为数学计算结果，不构成投资建议。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
