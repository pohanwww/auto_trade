#!/usr/bin/env python3
"""MA 策略出場參數優化腳本

針對 5m MA 均線糾纏策略的 rate 參數做 grid search，找出最佳組合。

用法：
    uv run python scripts/optimize_ma_rates.py
    uv run python scripts/optimize_ma_rates.py --start 2024-02-01 --end 2025-01-31
"""

import argparse
import itertools
from datetime import datetime, timedelta
from pathlib import Path

import yaml

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.engines.backtest_engine import BacktestEngine, BacktestEngineConfig
from auto_trade.models.trading_unit import TradingUnit
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.services.market_service import MarketService
from auto_trade.services.position_manager import PositionManagerConfig
from auto_trade.strategies import create_strategy


# 參數網格（rate 均為小數，如 0.004 = 0.4%）
# 完整搜尋（涵蓋 MA 窄範圍 + MACD 寬範圍）
# 3×4×4×3 = 144 組
PARAM_GRID_FULL = {
    "stop_loss_points_rate": [0.003, 0.004, 0.005],
    "trailing_stop_points_rate": [0.005, 0.006, 0.008, 0.01],
    "tighten_after_points_rate": [0.014, 0.016, 0.018, 0.021],
    "tightened_trailing_stop_points_rate": [0.003, 0.004, 0.005],
}
# 快速測試：固定 SL=0.004（已知穩定），只搜尋擴展範圍（27 組）
PARAM_GRID_QUICK = {
    "stop_loss_points_rate": [0.004],
    "trailing_stop_points_rate": [0.006, 0.008, 0.01],
    "tighten_after_points_rate": [0.014, 0.018, 0.021],
    "tightened_trailing_stop_points_rate": [0.003, 0.004, 0.005],
}


def load_base_config() -> dict:
    """從 strategy.yaml 載入 ma_conv_after_04 作為基底"""
    config_dir = Path(__file__).resolve().parent.parent / "config"
    with open(config_dir / "strategy.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["ma_conv_after_04"]


def build_unit_with_overrides(
    indicator_service: IndicatorService,
    base_config: dict,
    overrides: dict,
    name_suffix: str,
) -> TradingUnit:
    """建立帶參數覆寫的 TradingUnit"""
    trading = dict(base_config["trading"])
    trading.update(overrides)
    position = base_config.get("position", {})

    strategy_type = base_config.get("strategy_type", "ma_convergence")
    strategy_kwargs = {
        k: trading[k]
        for k in [
            "ema_periods", "convergence_threshold_pct", "convergence_min_bars",
            "max_bars_after_convergence", "allow_entry_during_convergence",
            "long_only", "volume_confirm", "cooldown_bars",
        ]
        if k in trading
    }
    strategy = create_strategy(strategy_type, indicator_service, **strategy_kwargs)
    pm_config = PositionManagerConfig.from_dict(trading, position)

    short_label = "_".join(f"{v}" for v in overrides.values()).replace(".", "")
    return TradingUnit(
        name=f"ma_rates_{name_suffix}_{short_label}",
        strategy=strategy,
        pm_config=pm_config,
    )


def main():
    parser = argparse.ArgumentParser(description="MA 策略 rate 參數優化")
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--save", action="store_true", help="儲存報告到 data/backtest/")
    parser.add_argument("--quick", action="store_true", help="快速模式（16 組代替 81 組）")
    args = parser.parse_args()

    param_grid = PARAM_GRID_QUICK if args.quick else PARAM_GRID_FULL

    # 日期
    end_date = datetime.strptime(args.end, "%Y-%m-%d") if args.end else datetime.now()
    start_date = (
        datetime.strptime(args.start, "%Y-%m-%d")
        if args.start
        else end_date - timedelta(days=args.days)
    )
    timeframe = "5m"

    print("=" * 70)
    print("🔧 MA 策略 rate 參數優化")
    print("=" * 70)
    print(f"📅 期間: {start_date:%Y-%m-%d} ~ {end_date:%Y-%m-%d}")
    print(f"⏱  時間尺度: {timeframe}")

    # 產生所有參數組合
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))
    print(f"🧪 共 {len(combos)} 組參數組合\n")

    config = Config()
    api_client = create_api_client(
        config.api_key,
        config.secret_key,
        config.ca_cert_path,
        config.ca_password,
        simulation=True,
    )
    market_service = MarketService(api_client)
    indicator_service = IndicatorService()
    base_config = load_base_config()

    # 建立所有 TradingUnit，並記錄每組的 overrides
    units = []
    overrides_by_name: dict[str, dict] = {}
    for i, combo in enumerate(combos):
        overrides = dict(zip(keys, combo, strict=True))
        unit = build_unit_with_overrides(
            indicator_service, base_config, overrides, name_suffix=str(i + 1)
        )
        units.append(unit)
        overrides_by_name[unit.name] = overrides

    # 執行回測
    bt_config = BacktestEngineConfig(
        symbol=config.symbol,
        sub_symbol=config.sub_symbol,
        start_date=start_date,
        end_date=end_date,
        timeframe=timeframe,
        initial_capital=1_000_000,
        slippage_points=0,
    )
    engine = BacktestEngine(bt_config, market_service, indicator_service)
    results = engine.run(units)

    # 輸出比較表
    rows = []
    for unit_name, r in results.items():
        # 從 unit_name 取出參數（或從 config）
        parts = unit_name.replace("ma_rates_", "").split("_")
        if len(parts) >= 5:
            param_str = " | ".join(f"{k.split('_')[-2][:2]}={parts[i]}" for i, k in enumerate(keys))
        else:
            param_str = unit_name
        rows.append({
            "name": unit_name,
            "param_str": param_str,
            "trades": r.total_trades,
            "win_rate": r.win_rate,
            "pnl_twd": r.total_pnl_twd,
            "pnl_pts": r.total_pnl_points,
            "profit_factor": r.profit_factor,
            "mdd": r.max_drawdown,
            "sharpe": r.sharpe_ratio,
        })

    # 依 PnL 排序
    rows.sort(key=lambda x: x["pnl_twd"], reverse=True)

    print("\n" + "=" * 100)
    print("📊 優化結果（依總盈虧排序）")
    print("=" * 100)
    print(f"{'排名':<4} {'總盈虧(TWD)':>12} {'Sharpe':>7} {'MDD':>8} {'勝率':>7} {'盈虧比':>7} {'交易數':>6}")
    print("-" * 100)
    for i, row in enumerate(rows[:20], 1):
        pf = row["profit_factor"] if row["profit_factor"] != float("inf") else 999
        print(
            f"{i:<4} {row['pnl_twd']:>+12,.0f} {row['sharpe']:>7.3f} "
            f"{row['mdd']:>7.1%} {row['win_rate']:>6.1%} {pf:>7.2f} {row['trades']:>6}"
        )
    if len(rows) > 20:
        print(f"  ... 其餘 {len(rows) - 20} 組略")

    # 最佳參數詳情
    best = rows[0]
    print("\n" + "=" * 100)
    print("🏆 最佳參數組合")
    print("=" * 100)
    best_overrides = overrides_by_name.get(best["name"], {})
    for k, v in best_overrides.items():
        print(f"  {k}: {v}")
    print(f"  總盈虧: {best['pnl_twd']:+,.0f} TWD")
    print(f"  夏普: {best['sharpe']:.3f}, MDD: {best['mdd']:.1%}")

    if args.save:
        engine.save_report(results)
        print("\n✅ 完整報告已儲存至 data/backtest/")


if __name__ == "__main__":
    main()
