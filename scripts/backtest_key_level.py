#!/usr/bin/env python3
"""Key Level Strategy parameter sweep backtester.

Usage:
    uv run python scripts/backtest_key_level.py --period consolidation --save
    uv run python scripts/backtest_key_level.py --period bull --save
    uv run python scripts/backtest_key_level.py --period both --grid trailing --save
"""

from __future__ import annotations

import argparse
import itertools
import os
from datetime import datetime

from auto_trade.core.config import Config  # must be first to resolve circular import
from auto_trade.core.client import create_api_client
from auto_trade.engines.backtest_engine import BacktestEngine, BacktestEngineConfig
from auto_trade.models.trading_unit import TradingUnit
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.services.market_service import MarketService
from auto_trade.services.position_manager import PositionManagerConfig
from auto_trade.strategies.key_level_strategy import KeyLevelStrategy


PERIODS = {
    # ── 盤整期 ──
    "con_quiet":  ("2023-06-01", "2023-09-30"),  # 低波動盤整 +0.1% Range 7.5%
    "con_wild":   ("2024-07-01", "2024-12-31"),  # 高波動盤整 +0.5% Range 24.5%
    "con_recent": ("2024-12-01", "2025-02-28"),  # 近期低波動 +0.4% Range 7.4%
    # ── 多頭趨勢 ──
    "bull_2024":  ("2024-02-01", "2024-03-31"),  # 穩定趨勢 +13.0%
    "bull_super": ("2025-06-01", "2025-10-31"),  # 超級趨勢 +34.8%（5個月）
    "bull_2026":  ("2026-01-02", "2026-02-28"),  # 新年暴漲 +20.8%
    # ── 空頭趨勢 ──
    "bear_2022":  ("2022-04-01", "2022-06-30"),  # 升息崩跌 -16.8%
    "bear_2025":  ("2025-03-01", "2025-04-30"),  # 急跌修正 -11.3%
    # ── 舊定義（保留向後兼容）──
    "2024": ("2024-01-01", "2024-12-31"),
    "2025": ("2025-01-01", "2025-12-31"),
    # ── 常用驗證期 ──
    "2025H2": ("2025-07-01", "2025-12-31"),
    "2026Q1": ("2026-01-01", "2026-03-20"),
    "202603": ("2026-03-01", "2026-03-20"),
}

LOTS = 4

PARAM_GRID_TRAILING = {
    "use_or": [True, False],
    "direction": ["long_only"],
    "max_trades_per_day": [2, 3],
    "breakout_buffer": [0.3],
    "bounce_buffer": [0.3],
    "entry_type": ["breakout_only", "both"],
    "tp_atr_multiplier": [0],
    "signal_level_count": [3, 7],
    "key_level_trail_mode": ["current", "previous"],
    "key_level_buffer": [0.10, 0.15, 0.20],
    "instant_threshold": [0.3],
    "session_mode": ["day_only", "day_night"],
    "leg_split": ["all_ts"],
}


OR_BARS_BY_TF = {"1m": 15, "5m": 3, "15m": 1, "30m": 1, "1h": 1}


def make_unit(
    indicator_service: IndicatorService,
    params: dict,
    unit_id: int,
    timeframe: str = "5m",
) -> TradingUnit:
    long_only = params["direction"] == "long_only"
    short_only = params["direction"] == "short_only"
    use_breakout = params["entry_type"] in ("both", "breakout_only")
    use_bounce = params["entry_type"] in ("both", "bounce_only")

    trail_mode = params.get("key_level_trail_mode", "current")
    session_mode = params.get("session_mode", "day_only")

    if session_mode == "day_night":
        entry_end_time = "04:30"
        session_end_time = "05:00"
        force_exit_time = "04:50"
        or_start_time = "08:45"
    elif session_mode == "night_only":
        entry_end_time = "04:30"
        session_end_time = "05:00"
        force_exit_time = "04:50"
        or_start_time = "15:00"
    else:
        entry_end_time = "12:30"
        session_end_time = "13:45"
        force_exit_time = "13:29"
        or_start_time = "08:45"


    or_bars = OR_BARS_BY_TF.get(timeframe, 3)

    strategy = KeyLevelStrategy(
        indicator_service,
        use_or=params["use_or"],
        or_bars=or_bars,
        or_start_time=or_start_time,
        entry_end_time=entry_end_time,
        session_end_time=session_end_time,
        swing_period=10,
        cluster_tolerance=50,
        zone_tolerance=50,
        signal_level_count=params.get("signal_level_count", 5),
        breakout_buffer=params["breakout_buffer"],
        bounce_buffer=params["bounce_buffer"],
        instant_threshold=params.get("instant_threshold", 0.3),
        atr_period=14,
        long_only=long_only,
        short_only=short_only,
        max_trades_per_day=params["max_trades_per_day"],
        max_trades_day_session=params.get("max_trades_day_session"),
        max_trades_night_session=params.get("max_trades_night_session"),
        sl_atr_multiplier=1.0,
        tp_atr_multiplier=params["tp_atr_multiplier"],
        key_level_buffer=params.get("key_level_buffer", 0.15),
        key_level_trail_mode=trail_mode,
        use_breakout=use_breakout,
        use_bounce=use_bounce,
        trend_filter=params.get("trend_filter", "or"),
        trend_filter_ema_period=params.get("trend_filter_ema_period", 200),
        timeframe=timeframe,
    )

    leg_split = params.get("leg_split", "all_ts")
    if leg_split == "all_ts":
        total_qty, tp_qty, ts_qty = LOTS, 0, LOTS
    elif leg_split == "split":
        total_qty, tp_qty, ts_qty = LOTS, LOTS // 2, LOTS - LOTS // 2
    else:
        total_qty, tp_qty, ts_qty = LOTS, LOTS, 0

    pm_config = PositionManagerConfig(
        total_quantity=total_qty,
        tp_leg_quantity=tp_qty,
        ts_leg_quantity=ts_qty,
        stop_loss_points=150,
        take_profit_points=300,
        start_trailing_stop_points=80,
        trailing_stop_points=60,
        timeframe=timeframe,
        enable_macd_fast_stop=False,
        force_exit_time=force_exit_time,
    )

    tf = params.get("trend_filter", "or")
    if tf == "ema":
        ema_p = params.get("trend_filter_ema_period", 200)
        or_tag = f"EMA{ema_p}"
    elif tf == "none":
        or_tag = "NoFilt"
    elif params["use_or"]:
        or_tag = "OR"
    else:
        or_tag = "Pure"
    dir_tag = {"both": "B", "long_only": "L", "short_only": "S"}[params["direction"]]
    entry_tag = {"both": "BK+BC", "breakout_only": "BK", "bounce_only": "BC"}[
        params["entry_type"]
    ]
    sig_n = params.get("signal_level_count", 5)
    kl_buf = params.get("key_level_buffer", 0.15)
    trail_tag = "prev" if trail_mode == "previous" else "cur"
    sess_tag = (
        "D+N" if session_mode == "day_night"
        else "N" if session_mode == "night_only"
        else "D"
    )
    buf_str = f"{kl_buf:.0f}pt" if kl_buf >= 1 else f"{kl_buf:.2f}×ATR"
    bb = params.get("breakout_buffer", 0.3)
    ib = params.get("instant_threshold", 0.3)
    bb_ib_tag = f"bb={bb:.2f}/ib={ib:.2f}"
    max_day = params.get("max_trades_day_session")
    max_night = params.get("max_trades_night_session")
    if max_day is not None or max_night is not None:
        max_tag = f"day{max_day}/night{max_night}"
    else:
        max_tag = f"max{params['max_trades_per_day']}/d"
    name = (
        f"#{unit_id:03d} {or_tag} {dir_tag} {sess_tag} "
        f"{entry_tag} {trail_tag} buf={buf_str} {bb_ib_tag} "
        f"{max_tag} n={sig_n}"
    )

    return TradingUnit(name=name, strategy=strategy, pm_config=pm_config)


def format_summary_table(sorted_results: list, period_name: str) -> str:
    """Format results into a summary text block."""
    lines: list[str] = []
    lines.append(f"{'=' * 120}")
    lines.append(f"  RESULTS SUMMARY — {period_name}")
    lines.append(f"{'=' * 120}")
    header = (
        f"{'Rank':<5} {'Name':<65} {'PnL(TWD)':>10} "
        f"{'Trades':>7} {'Win':>5} {'Loss':>5} {'WR%':>6} "
        f"{'PF':>6} {'Sharpe':>7} {'MaxDD%':>7} {'Calmar':>7} {'AvgDur':>8}"
    )
    lines.append(header)
    lines.append("-" * 155)

    for rank, (name, res) in enumerate(sorted_results, 1):
        wr = (res.win_rate * 100) if res.win_rate else 0.0
        sharpe = res.sharpe_ratio if res.sharpe_ratio else 0.0
        pf = res.profit_factor if res.profit_factor else 0.0
        avg_dur = res.avg_trade_duration_hours if res.avg_trade_duration_hours else 0.0
        mdd_pct = (res.max_drawdown * 100) if res.max_drawdown else 0.0
        calmar = res.calmar_ratio if res.calmar_ratio else 0.0
        lines.append(
            f"{rank:<5} {name:<65} {res.total_pnl_twd:>10,.0f} "
            f"{res.total_trades:>7} {res.winning_trades:>5} {res.losing_trades:>5} "
            f"{wr:>5.1f}% {pf:>6.2f} {sharpe:>7.2f} "
            f"{mdd_pct:>6.2f}% {calmar:>7.2f} {avg_dur:>7.1f}h"
        )

    return "\n".join(lines)


def format_trade_details(sorted_results: list, top_n: int = 10) -> str:
    """Format individual trade details for top N configs."""
    lines: list[str] = []
    for rank, (name, res) in enumerate(sorted_results[:top_n], 1):
        lines.append(f"\n{'=' * 100}")
        lines.append(f"  #{rank} {name}")
        wr = (res.win_rate * 100) if res.win_rate else 0.0
        mdd = (res.max_drawdown * 100) if res.max_drawdown else 0.0
        lines.append(
            f"  PnL={res.total_pnl_twd:,.0f} TWD | "
            f"Trades={res.total_trades} | WR={wr:.1f}% | "
            f"Sharpe={res.sharpe_ratio:.2f} | "
            f"PF={res.profit_factor:.2f} | "
            f"MaxDD={mdd:.2f}% | "
            f"Gross+={res.gross_profit:,.0f} Gross-={res.gross_loss:,.0f}"
        )
        lines.append(f"{'=' * 100}")
        lines.append(
            f"  {'#':<4} {'Entry Time':<20} {'Dir':<6} {'Entry':>8} "
            f"{'Exit':>8} {'PnL(pts)':>10} {'PnL(TWD)':>10} {'Exit Reason':<15} {'Duration':<12}"
        )
        lines.append(f"  {'-' * 110}")

        for i, trade in enumerate(res.trades, 1):
            from auto_trade.models.account import Action
            direction = "LONG" if trade.action == Action.Buy else "SHORT"
            entry_t = trade.entry_time.strftime("%Y-%m-%d %H:%M") if trade.entry_time else "?"
            exit_p = trade.exit_price or 0
            pnl_pts = trade.pnl_points or 0
            pnl_twd = trade.pnl_twd or 0
            exit_r = str(trade.exit_reason.value) if trade.exit_reason else "?"
            dur = ""
            if trade.entry_time and trade.exit_time:
                delta = trade.exit_time - trade.entry_time
                hours = delta.total_seconds() / 3600
                dur = f"{hours:.1f}h"
            lines.append(
                f"  {i:<4} {entry_t:<20} {direction:<6} {trade.entry_price:>8,.0f} "
                f"{exit_p:>8,.0f} {pnl_pts:>10,.0f} "
                f"{pnl_twd:>10,.0f} {exit_r:<15} {dur:<12}"
            )

    return "\n".join(lines)


def run_sweep(
    period_name: str,
    start_str: str,
    end_str: str,
    max_combos: int = 50,
    save: bool = False,
    param_grid: dict | None = None,
    specific_params: list[dict] | None = None,
    slippage: int = 1,
    timeframe: str = "5m",
):
    """Run a parameter sweep.

    If *specific_params* is given, those exact parameter dicts are used
    (ignoring param_grid / max_combos).
    """
    start_date = datetime.strptime(start_str, "%Y-%m-%d")
    end_date = datetime.strptime(end_str, "%Y-%m-%d")

    print(f"\n{'=' * 70}")
    print(f"  Period: {period_name}  ({start_str} ~ {end_str})")
    print(f"{'=' * 70}")

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

    if specific_params is not None:
        all_combos = specific_params
        total_grid_size = len(all_combos)
        sampled = False
    else:
        if param_grid is None:
            param_grid = PARAM_GRID_TRAILING
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        all_combos = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
        total_grid_size = len(all_combos)
        sampled = False
        if total_grid_size > max_combos:
            print(f"  Grid has {total_grid_size} combos, sampling {max_combos}")
            import random
            random.seed(42)
            all_combos = random.sample(all_combos, max_combos)
            sampled = True
        else:
            print(f"  Grid has {total_grid_size} combos (running all)")

    units = [
        make_unit(indicator_service, params, i, timeframe=timeframe)
        for i, params in enumerate(all_combos, 1)
    ]

    print(f"\n  Running {len(units)} configurations ({LOTS} lots MXF, {timeframe})...")

    bt_config = BacktestEngineConfig(
        symbol=config.symbol,
        sub_symbol=config.sub_symbol,
        start_date=start_date,
        end_date=end_date,
        timeframe=timeframe,
        initial_capital=1_000_000.0,
        slippage_points=slippage,
    )

    engine = BacktestEngine(bt_config, market_service, indicator_service)
    results = engine.run(units)

    if not results:
        print("  No results.")
        return None, []

    sorted_results = sorted(
        results.items(),
        key=lambda x: x[1].total_pnl_twd,
        reverse=True,
    )

    summary = format_summary_table(sorted_results, period_name)
    print(f"\n{summary}")

    # Buy & Hold benchmark (4 lots)
    benchmark = engine._get_benchmark_data(quantity=LOTS)
    bh_line = ""
    if benchmark:
        bh_line = (
            f"  📌 Buy & Hold ({LOTS} lots): "
            f"{benchmark['entry_price']} → {benchmark['exit_price']} | "
            f"PnL={benchmark['pnl_twd']:+,.0f} TWD | "
            f"Return={benchmark['return_pct']:+.2f}% | "
            f"MaxDD={benchmark['max_drawdown']:.2%}"
        )
        print(f"\n{bh_line}")

    # Top 5 console
    print(f"\n  TOP 5 — {period_name}:")
    for i, (name, res) in enumerate(sorted_results[:5], 1):
        wr = (res.win_rate * 100) if res.win_rate else 0.0
        mdd = (res.max_drawdown * 100) if res.max_drawdown else 0.0
        print(f"    {i}. {name}")
        print(
            f"       PnL={res.total_pnl_twd:,.0f}  Trades={res.total_trades} "
            f" WR={wr:.1f}%  MaxDD={mdd:.2f}%  Sharpe={res.sharpe_ratio:.2f}"
        )

    if save:
        save_dir = "data/backtest"
        os.makedirs(save_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{save_dir}/key_level_sweep_{period_name}_trailing_{timestamp}.txt"

        report_lines: list[str] = []
        report_lines.append("=" * 120)
        report_lines.append("  KEY LEVEL STRATEGY — BACKTEST SWEEP REPORT")
        report_lines.append("=" * 120)
        report_lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append(f"  Period: {period_name} ({start_str} ~ {end_str})")
        report_lines.append(f"  Total grid: {total_grid_size}, tested: {len(units)}, sampled: {sampled}")
        report_lines.append(f"  Symbol: {config.symbol} ({config.sub_symbol}) | Lots: {LOTS}")
        report_lines.append(f"  Timeframe: {timeframe} | Slippage: {slippage}pt | Capital: 1,000,000 TWD")
        report_lines.append("")
        if bh_line:
            report_lines.append(bh_line)
            report_lines.append("")
        report_lines.append(summary)
        report_lines.append("")
        report_lines.append("\n" + "=" * 120)
        report_lines.append("  DETAILED TRADE LOG — TOP 10 CONFIGURATIONS")
        report_lines.append("=" * 120)
        report_lines.append(format_trade_details(sorted_results, top_n=10))

        with open(filename, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))
        print(f"\n💾 完整報告已儲存: {filename}")

    return sorted_results, all_combos


_FIXED = {
    "breakout_buffer": 0.3,
    "bounce_buffer": 0.3,
    "tp_atr_multiplier": 0,
    "sl_atr_multiplier": 1.0,
    "leg_split": "all_ts",
}

def _p(use_or, session, entry, trail, buf, maxt, n, direction="long_only"):
    return {
        **_FIXED,
        "direction": direction,
        "use_or": use_or,
        "session_mode": session,
        "entry_type": entry,
        "key_level_trail_mode": trail,
        "key_level_buffer": buf,
        "instant_threshold": 0.3,
        "max_trades_per_day": maxt,
        "signal_level_count": n,
    }

TOP_PARAMS = [
    # buf = ATR ratio (0.15 ≈ 10pts when ATR≈65)
    #         use_or  session       entry            trail       buf   maxt n  direction
    _p(True,  "day_only", "breakout_only", "previous", 0.15, 2, 7, "both"),        # 01 OR B BK
    _p(True,  "day_only", "both",          "previous", 0.15, 2, 7, "both"),        # 02 OR B BK+BC
    _p(False, "day_only", "breakout_only", "previous", 0.15, 2, 7, "long_only"),   # 03 Pure L BK
    _p(False, "day_only", "both",          "previous", 0.15, 2, 7, "long_only"),   # 04 Pure L BK+BC
    _p(True,  "day_only", "breakout_only", "previous", 0.15, 2, 7, "long_only"),   # 05 OR L BK  ← current
    _p(True,  "day_only", "both",          "previous", 0.15, 2, 7, "long_only"),   # 06 OR L BK+BC
]

TOP10_PARAMS = TOP_PARAMS

TRAIL_ANCHOR_PARAMS = [
    _p(True, "day_only",   "breakout_only", "previous", 0.15, 2, 7, "both"),
    _p(True, "day_only",   "breakout_only", "previous", 0.15, 2, 7, "long_only"),
    _p(True, "night_only", "breakout_only", "previous", 0.15, 2, 7, "both"),
    _p(True, "night_only", "breakout_only", "previous", 0.15, 2, 7, "long_only"),
]

KL_BUF_PARAMS = [
    cfg
    for buf in [0.15, 0.20, 0.25, 0.30]
    for cfg in [
        _p(True,  "day_only",   "breakout_only", "previous", buf, 2, 7, "long_only"),
        _p(True,  "day_only",   "breakout_only", "previous", buf, 2, 7, "both"),
        _p(True,  "night_only", "breakout_only", "previous", buf, 2, 7, "long_only"),
        _p(True,  "night_only", "breakout_only", "previous", buf, 2, 7, "both"),
    ]
]


def _pb(use_or, session, direction, bb, ib, trend_filter="or"):
    """Helper for instant buffer sweep: build param dict with specific bb/ib."""
    return {
        **_FIXED,
        "breakout_buffer": bb,
        "bounce_buffer": 0.3,
        "instant_threshold": ib,
        "direction": direction,
        "use_or": use_or,
        "session_mode": session,
        "entry_type": "breakout_only",
        "key_level_trail_mode": "previous",
        "key_level_buffer": 0.15,
        "max_trades_per_day": 2,
        "signal_level_count": 7,
        "trend_filter": trend_filter,
    }



def _make_instant_buf_params(fine: bool = False) -> list[dict]:
    """Generate instant_threshold vs breakout_buffer sweep combos.

    fine=True: narrow grid around bb=0.30, ib=0.30 sweet spot.
    """
    if fine:
        combos = [
            (0.20, 0.20), (0.20, 0.30), (0.20, 0.40),
            (0.25, 0.25), (0.25, 0.30), (0.25, 0.40),
            (0.30, 0.30), (0.30, 0.40),
            (0.35, 0.30), (0.35, 0.35),
            (0.40, 0.30), (0.40, 0.40),
        ]
    else:
        combos = [
            (0.15, 0.3),
            (0.3, 0.3),   # baseline
            (0.3, 0.5),
            (0.3, 0.7),
        ]
    params = []
    for bb, ib in combos:
        params.append(_pb(True, "day_only", "both", bb, ib))
        params.append(_pb(True, "day_only", "long_only", bb, ib))
        params.append(_pb(True, "night_only", "both", bb, ib, trend_filter="or"))
    return params


INSTANT_BUF_PARAMS = _make_instant_buf_params()
INSTANT_BUF_FINE_PARAMS = _make_instant_buf_params(fine=True)


def _make_nopvt_sweep() -> list[dict]:
    """Comprehensive buffer sweep.

    Grid: bb × ib combinations (symmetric + asymmetric where ib >= bb)
    × 4 strategy configs (L/B × D/N).
    """
    combos = [
        # symmetric
        (0.15, 0.15),
        (0.20, 0.20),
        (0.30, 0.30),  # baseline
        (0.40, 0.40),
        # ib > bb (instant is looser = more selective)
        (0.15, 0.30),
        (0.20, 0.30),
        (0.20, 0.40),
        (0.30, 0.40),
        (0.30, 0.50),
        # bb > ib (instant is tighter = more aggressive)
        (0.30, 0.15),
        (0.30, 0.20),
        (0.40, 0.20),
    ]
    params = []
    for bb, ib in combos:
        params.append(_pb(True, "day_only",   "long_only", bb, ib))
        params.append(_pb(True, "night_only", "long_only", bb, ib, trend_filter="or"))
        params.append(_pb(True, "day_only",   "both",      bb, ib))
        params.append(_pb(True, "night_only", "both",      bb, ib, trend_filter="or"))
    return params


NOPVT_SWEEP_PARAMS = _make_nopvt_sweep()

# 2-config subset for multi-timeframe testing (Pure L + OR B)
MTF_PARAMS = [
    _p(False, "day_only", "breakout_only", "previous", 0.15, 2, 7, "long_only"),   # Pure L
    _p(True,  "day_only", "breakout_only", "previous", 0.15, 2, 7, "both"),        # OR B
]


def parse_args():
    parser = argparse.ArgumentParser(description="Key Level Strategy Sweep")
    parser.add_argument(
        "--period",
        choices=list(PERIODS.keys()) + ["all_con", "all_bull", "all_bear", "validate"],
        default="con_quiet",
        help="Period to test. all_con/all_bull/all_bear = run all of that type.",
    )
    parser.add_argument(
        "--grid",
        choices=["trailing", "instant_buf", "instant_buf_fine", "nopvt_sweep", "trail_anchor", "kl_buf"],
        default=None,
        help="Parameter grid to use for sweep.",
    )
    parser.add_argument(
        "--max-combos",
        type=int,
        default=80,
        help="Max parameter combinations to test",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save full backtest report to data/backtest/",
    )
    parser.add_argument(
        "--slippage",
        type=int,
        default=1,
        help="Slippage in points (default 1)",
    )
    parser.add_argument(
        "--timeframe",
        choices=["1m", "5m", "15m", "30m", "1h"],
        default="5m",
        help="K-bar timeframe (default 5m)",
    )
    parser.add_argument(
        "--mtf",
        action="store_true",
        help="Run multi-timeframe sweep: 3 periods × 4 TFs × 2 strategies (Pure L + OR B)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.mtf:
        _run_mtf(args)
        return

    if args.period == "validate":
        _run_validate(args)
        return

    GROUP_MAP = {
        "all_con":  ["con_quiet", "con_wild", "con_recent"],
        "all_bull": ["bull_2024", "bull_super", "bull_2026"],
        "all_bear": ["bear_2022", "bear_2025"],
    }

    if args.period in GROUP_MAP:
        period_keys = GROUP_MAP[args.period]
    else:
        period_keys = [args.period]

    periods_to_run = [(k, PERIODS[k]) for k in period_keys]

    # Select params based on grid
    if args.grid == "instant_buf":
        sweep_params = INSTANT_BUF_PARAMS
    elif args.grid == "instant_buf_fine":
        sweep_params = INSTANT_BUF_FINE_PARAMS
    elif args.grid == "nopvt_sweep":
        sweep_params = NOPVT_SWEEP_PARAMS
    elif args.grid == "trail_anchor":
        sweep_params = TRAIL_ANCHOR_PARAMS
    elif args.grid == "kl_buf":
        sweep_params = KL_BUF_PARAMS
    else:
        sweep_params = TOP10_PARAMS

    slip = args.slippage
    tf = args.timeframe
    for period_name, (start, end) in periods_to_run:
        tag = f"{period_name}_slip{slip}" if slip != 1 else period_name
        if tf != "5m":
            tag = f"{tag}_{tf}"
        if args.grid:
            tag = f"{tag}_{args.grid}"
        run_sweep(
            tag, start, end,
            max_combos=args.max_combos,
            save=args.save,
            specific_params=sweep_params,
            slippage=slip,
            timeframe=tf,
        )


def _run_mtf(args):
    """Run multi-timeframe sweep: 3 periods × 4 TFs × 2 strategies."""
    MTF_PERIODS = ["con_quiet", "bull_super", "bear_2022"]
    TFS = ["5m", "15m", "30m", "1h"]

    save = args.save
    slip = args.slippage

    print("\n" + "=" * 80)
    print("  MULTI-TIMEFRAME SWEEP")
    print(f"  Periods: {MTF_PERIODS}")
    print(f"  Timeframes: {TFS}")
    print(f"  Strategies: Pure L + OR B  ({len(MTF_PARAMS)} configs)")
    print(f"  Total runs: {len(MTF_PERIODS) * len(TFS)} = "
          f"{len(MTF_PERIODS)} × {len(TFS)}")
    print("=" * 80)

    all_results = {}
    for pname in MTF_PERIODS:
        s, e = PERIODS[pname]
        for tf in TFS:
            tag = f"{pname}_{tf}"
            res, _ = run_sweep(
                tag, s, e,
                save=save,
                specific_params=MTF_PARAMS,
                slippage=slip,
                timeframe=tf,
            )
            if res:
                all_results[tag] = res

    # Summary comparison
    print("\n" + "=" * 120)
    print("  MULTI-TIMEFRAME COMPARISON SUMMARY")
    print("=" * 120)
    header = f"{'Period':<14} {'TF':<5} {'Config':<30} {'PnL':>12} {'WinRate':>8} {'Trades':>7} {'MaxDD':>10} {'Sharpe':>8}"
    print(header)
    print("-" * 120)
    for key in sorted(all_results.keys()):
        for name, result in all_results[key]:
            print(
                f"{key:<14} {'':5} {name:<30} "
                f"{result.total_pnl_twd:>12,.0f} "
                f"{result.win_rate:>7.1f}% "
                f"{result.total_trades:>7} "
                f"{result.max_drawdown_pct:>9.2f}% "
                f"{result.sharpe_ratio:>8.2f}"
            )
    print("=" * 120)


def _run_validate(args):
    """Run slippage sensitivity test across 2024/2025."""
    save = args.save
    params = TOP10_PARAMS

    print(f"\n  Slippage sensitivity test: {len(params)} configs × multiple slippage levels...")

    tf = args.timeframe
    all_sorted = {}
    for slip in [1, 2, 3, 5]:
        for pname in ["2024", "2025"]:
            s, e = PERIODS[pname]
            tag = f"{pname}_slip{slip}"
            res, _ = run_sweep(tag, s, e, save=save, specific_params=params, slippage=slip, timeframe=tf)
            if res:
                all_sorted[tag] = res

    if len(all_sorted) >= 2:
        _generate_final_report(
            all_sorted.get("consolidation"),
            all_sorted.get("bull"),
            all_sorted.get("2024"),
            all_sorted.get("2025"),
            save=save,
        )


def _generate_final_report(con_results, bull_results, res_2024, res_2025, save=False):
    """Generate the ultimate cross-period comparison report."""
    lines: list[str] = []
    lines.append("\n" + "=" * 140)
    lines.append("  FINAL CROSS-PERIOD REPORT")
    lines.append("=" * 140)

    all_periods = {
        "consolidation": {n: r for n, r in con_results} if con_results else {},
        "bull": {n: r for n, r in bull_results} if bull_results else {},
        "2024": {n: r for n, r in res_2024} if res_2024 else {},
        "2025": {n: r for n, r in res_2025} if res_2025 else {},
    }

    # Find names present in both validation periods
    names_2024 = set(all_periods["2024"].keys())
    names_2025 = set(all_periods["2025"].keys())
    common = names_2024 & names_2025
    if not common:
        common = names_2024 | names_2025

    def _pnl(period, name):
        r = all_periods[period].get(name)
        return r.total_pnl_twd if r else 0

    def _wr(period, name):
        r = all_periods[period].get(name)
        return (r.win_rate * 100) if r and r.win_rate else 0.0

    def _mdd(period, name):
        r = all_periods[period].get(name)
        return (r.max_drawdown * 100) if r and r.max_drawdown else 0.0

    def _sharpe(period, name):
        r = all_periods[period].get(name)
        return r.sharpe_ratio if r and r.sharpe_ratio else 0.0

    ranked = []
    for name in common:
        total = sum(_pnl(p, name) for p in all_periods)
        ranked.append((name, total))
    ranked.sort(key=lambda x: x[1], reverse=True)

    header = (
        f"{'Rank':<5} {'Name':<55} "
        f"{'Con PnL':>10} {'Bull PnL':>10} "
        f"{'2024 PnL':>10} {'2025 PnL':>10} "
        f"{'TOTAL':>12} {'2024 WR':>7} {'2025 WR':>7} "
        f"{'2024 MDD':>8} {'2025 MDD':>8} "
        f"{'2024 Sh':>7} {'2025 Sh':>7}"
    )
    lines.append(header)
    lines.append("-" * 155)

    for rank, (name, total) in enumerate(ranked[:30], 1):
        lines.append(
            f"{rank:<5} {name:<55} "
            f"{_pnl('consolidation', name):>10,.0f} {_pnl('bull', name):>10,.0f} "
            f"{_pnl('2024', name):>10,.0f} {_pnl('2025', name):>10,.0f} "
            f"{total:>12,.0f} {_wr('2024', name):>6.1f}% {_wr('2025', name):>6.1f}% "
            f"{_mdd('2024', name):>7.2f}% {_mdd('2025', name):>7.2f}% "
            f"{_sharpe('2024', name):>7.2f} {_sharpe('2025', name):>7.2f}"
        )

    report_text = "\n".join(lines)
    print(report_text)

    if save:
        save_dir = "data/backtest"
        os.makedirs(save_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{save_dir}/key_level_FINAL_REPORT_{timestamp}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"\n💾 最終跨期報告已儲存: {filename}")


if __name__ == "__main__":
    main()
