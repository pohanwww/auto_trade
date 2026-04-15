#!/usr/bin/env python3
"""Structural Profit Lock (PL) — dedicated backtest CLI.

Loads the same sweep helpers as scripts/backtest_key_level.py (no package import).

Examples:
  uv run python scripts/backtest_profit_lock.py --period 2025H2 --compare
  uv run python scripts/backtest_profit_lock.py --period con_quiet --sweep --save
  uv run python scripts/backtest_profit_lock.py --period bull_super \\
    --session night_only --slippage 1 --timeframe 5m
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def _load_btkl():
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    for p in (src, root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    path = root / "scripts" / "backtest_key_level.py"
    spec = importlib.util.spec_from_file_location("btkl", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _apply_session_trade_limits(base: dict, session: str) -> None:
    """Align max trade limits with live config defaults."""
    if session == "day_only":
        base["max_trades_per_day"] = 2
        base.pop("max_trades_day_session", None)
        base.pop("max_trades_night_session", None)
    elif session == "night_only":
        base["max_trades_per_day"] = 3
        base.pop("max_trades_day_session", None)
        base.pop("max_trades_night_session", None)
    else:
        base["max_trades_per_day"] = 3
        base["max_trades_day_session"] = 2
        base["max_trades_night_session"] = 3


def _build_compare_params(
    session: str, btkl, *, direction: str, pl_side: str, pressure_mode: str,
) -> list[dict]:
    base = btkl._p(
        True, session, "breakout_only", "previous", 0.15, 2, 7, direction,
    )
    base["trend_filter"] = "or"
    base["kl_exhausted_atr_multiplier"] = 0.5
    base["profit_lock_pressure_mode"] = pressure_mode
    # Pressure distance gate based on entry-price percentage.
    base["profit_lock_def3_price_pct"] = 0.005
    # Enable PL debug by default for this research script.
    base["profit_lock_debug"] = True
    _apply_session_trade_limits(base, session)
    pl_long_only = pl_side == "long_only"
    return [
        {**base, "enable_profit_lock": False},
        {
            **base,
            "enable_profit_lock": True,
            "profit_lock_long_only": pl_long_only,
        },
    ]


def _build_sweep_params(
    session: str, btkl, *, direction: str, pl_side: str, pressure_mode: str,
) -> list[dict]:
    base = btkl._p(
        True, session, "breakout_only", "previous", 0.15, 2, 7, direction,
    )
    base["trend_filter"] = "or"
    base["kl_exhausted_atr_multiplier"] = 0.5
    base["profit_lock_pressure_mode"] = pressure_mode
    base["profit_lock_debug"] = True
    _apply_session_trade_limits(base, session)
    pl_long_only = pl_side == "long_only"
    out: list[dict] = [{**base, "enable_profit_lock": False}]
    for lb in (8, 12, 16, 20):
        for rk in (3, 4):  # user decision: do not test rank=2
            # Entry-price percentage thresholds for def3/def4 gates.
            for pct in (0.005, 0.007):
                out.append({
                    **base,
                    "enable_profit_lock": True,
                    "profit_lock_long_only": pl_long_only,
                    "profit_lock_lookback_bars": lb,
                    "profit_lock_atr_rank_max": rk,
                    "profit_lock_def3_price_pct": pct,
                })
    return out


def main() -> None:
    btkl = _load_btkl()
    periods = sorted(btkl.PERIODS.keys())

    p = argparse.ArgumentParser(
        description="Backtest structural Profit Lock (PL) vs baseline.",
    )
    p.add_argument(
        "--period",
        required=False,
        default="2025H2",
        choices=periods,
        help="Named date range (same keys as backtest_key_level.py PERIODS).",
    )
    p.add_argument(
        "--session",
        default="day_only",
        choices=["day_only", "night_only", "day_night"],
        help="Session template for OR / force_exit (default day_only).",
    )
    p.add_argument(
        "--compare",
        action="store_true",
        help="Run exactly 2 units: PL off vs PL on.",
    )
    p.add_argument(
        "--sweep",
        action="store_true",
        help="Run baseline + small PL grid (lookback × rank × gap).",
    )
    p.add_argument(
        "--std-matrix",
        action="store_true",
        help=(
            "Run standard matrix: 3 periods (2025H2/2026Q1/202603) × "
            "2 sessions (day/night) × compare(PL off vs on)."
        ),
    )
    p.add_argument(
        "--direction",
        default="long_only",
        choices=["long_only", "both"],
        help="Strategy direction for compare/sweep matrix.",
    )
    p.add_argument(
        "--pl-side",
        default="long_only",
        choices=["long_only", "both"],
        help="PL side when enabled (long_only or both).",
    )
    p.add_argument(
        "--pressure-mode",
        default="any",
        choices=["any", "def1", "def3", "def4"],
        help="Pressure gate mode: any (OR) or single ablation def1/def3/def4.",
    )
    p.add_argument(
        "--timeframe",
        default="5m",
        choices=["1m", "5m", "15m", "30m", "1h"],
    )
    p.add_argument("--slippage", type=int, default=1)
    p.add_argument(
        "--save",
        action="store_true",
        help="Write full report under data/backtest/ (same as backtest_key_level).",
    )
    args = p.parse_args()

    if args.std_matrix:
        periods_to_run = ["2025H2", "2026Q1", "202603"]
        sessions_to_run = ["day_only", "night_only"]
        for period in periods_to_run:
            start_str, end_str = btkl.PERIODS[period]
            for session in sessions_to_run:
                base_tag = "_".join(
                    [
                        period,
                        session,
                        args.direction,
                        f"pl{args.pl_side}",
                        f"pm{args.pressure_mode}",
                        args.timeframe,
                        f"slip{args.slippage}",
                    ],
                )
                btkl.run_sweep(
                    f"{base_tag}_pl_compare",
                    start_str,
                    end_str,
                    save=args.save,
                    specific_params=_build_compare_params(
                        session,
                        btkl,
                        direction=args.direction,
                        pl_side=args.pl_side,
                        pressure_mode=args.pressure_mode,
                    ),
                    slippage=args.slippage,
                    timeframe=args.timeframe,
                )
        return

    if not args.compare and not args.sweep:
        p.error("Specify --compare and/or --sweep, or use --std-matrix.")

    start_str, end_str = btkl.PERIODS[args.period]
    base_tag = "_".join(
        [
            args.period,
            args.session,
            args.direction,
            f"pl{args.pl_side}",
            f"pm{args.pressure_mode}",
            args.timeframe,
            f"slip{args.slippage}",
        ],
    )

    if args.compare:
        btkl.run_sweep(
            f"{base_tag}_pl_compare",
            start_str,
            end_str,
            save=args.save,
            specific_params=_build_compare_params(
                args.session,
                btkl,
                direction=args.direction,
                pl_side=args.pl_side,
                pressure_mode=args.pressure_mode,
            ),
            slippage=args.slippage,
            timeframe=args.timeframe,
        )
    if args.sweep:
        btkl.run_sweep(
            f"{base_tag}_pl_sweep",
            start_str,
            end_str,
            save=args.save,
            specific_params=_build_sweep_params(
                args.session,
                btkl,
                direction=args.direction,
                pl_side=args.pl_side,
                pressure_mode=args.pressure_mode,
            ),
            slippage=args.slippage,
            timeframe=args.timeframe,
        )


if __name__ == "__main__":
    main()
