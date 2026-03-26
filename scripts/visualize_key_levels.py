#!/usr/bin/env python3
"""Visualize Confluence Key Level Detection on synthetic TAIEX-futures-like data.

Generates realistic 5-min K-bar data for:
  - Previous day session   (08:45 ~ 13:45)
  - Previous night session  (15:00 ~ 05:00 next day)
  - Today's opening range   (08:45 ~ 09:00)

Then runs the full confluence detector and plots:
  1. Candlestick chart with key level lines (colour-coded by method count)
  2. Console table of all detected levels with scores/sources

Usage:
    cd <project_root>
    .venv/bin/python scripts/visualize_key_levels.py
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
from matplotlib.patches import FancyBboxPatch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from auto_trade.models.market import KBar
from auto_trade.services.key_level_detector import (
    KeyLevel,
    SessionData,
    find_confluence_levels,
)


# ──────────────────────────────────────────────
# Synthetic data generator
# ──────────────────────────────────────────────

def _generate_session_kbars(
    start: datetime,
    end: datetime,
    interval_min: int,
    open_price: float,
    volatility: float = 15.0,
    trend: float = 0.0,
    bounce_levels: list[float] | None = None,
    volume_base: int = 200,
) -> list[KBar]:
    """Generate synthetic K-bars with optional bounce levels (simulated S/R)."""
    kbars: list[KBar] = []
    price = open_price
    t = start

    while t < end:
        drift = trend + random.gauss(0, volatility)
        intra_high = price + abs(random.gauss(0, volatility * 0.7))
        intra_low = price - abs(random.gauss(0, volatility * 0.7))

        # Simulate bounces off key levels
        if bounce_levels:
            for bl in bounce_levels:
                if intra_low <= bl <= price:
                    # Bounce up from support
                    intra_low = bl - random.uniform(0, 5)
                    drift = abs(drift) * 0.5
                elif price <= bl <= intra_high:
                    # Reject from resistance
                    intra_high = bl + random.uniform(0, 5)
                    drift = -abs(drift) * 0.5

        close = price + drift
        o = price
        h = max(o, close, intra_high)
        lo = min(o, close, intra_low)
        vol = max(10, int(volume_base + random.gauss(0, volume_base * 0.4)))

        kbars.append(KBar(time=t, open=o, high=h, low=lo, close=close, volume=vol))
        price = close
        t += timedelta(minutes=interval_min)

    return kbars


def generate_test_data() -> tuple[list[KBar], list[KBar], list[KBar], dict]:
    """Generate prev_day, prev_night, today_open K-bars + metadata.

    Creates data where certain price levels are deliberately touched
    multiple times to test swing cluster detection.
    """
    random.seed(42)

    base = 22000.0
    # Key support/resistance levels we embed on purpose
    embedded_levels = [21900.0, 22050.0, 22200.0, 22350.0]

    # --- Previous Day Session (08:45 ~ 13:45) ---
    prev_day_start = datetime(2026, 3, 19, 8, 45)
    prev_day_end = datetime(2026, 3, 19, 13, 45)
    prev_day_kbars = _generate_session_kbars(
        start=prev_day_start,
        end=prev_day_end,
        interval_min=5,
        open_price=base,
        volatility=12.0,
        trend=0.3,
        bounce_levels=embedded_levels,
        volume_base=300,
    )

    prev_day_close = prev_day_kbars[-1].close if prev_day_kbars else base

    # --- Previous Night Session (15:00 ~ 05:00 next day) ---
    prev_night_start = datetime(2026, 3, 19, 15, 0)
    prev_night_end = datetime(2026, 3, 20, 5, 0)
    gap_from_day = random.uniform(-30, 30)
    prev_night_kbars = _generate_session_kbars(
        start=prev_night_start,
        end=prev_night_end,
        interval_min=5,
        open_price=prev_day_close + gap_from_day,
        volatility=10.0,
        trend=-0.1,
        bounce_levels=embedded_levels,
        volume_base=150,
    )

    prev_night_close = prev_night_kbars[-1].close if prev_night_kbars else prev_day_close

    # --- Today Opening (08:45 ~ 09:00, 3 bars) ---
    today_gap = random.uniform(-20, 40)
    today_open_price = prev_night_close + today_gap
    today_start = datetime(2026, 3, 20, 8, 45)
    today_end = datetime(2026, 3, 20, 9, 0)
    today_kbars = _generate_session_kbars(
        start=today_start,
        end=today_end,
        interval_min=5,
        open_price=today_open_price,
        volatility=8.0,
        trend=0.5,
        bounce_levels=embedded_levels,
        volume_base=400,
    )

    # Compute OHLC summaries
    def ohlc(bars: list[KBar]) -> dict:
        return {
            "open": int(bars[0].open),
            "high": int(max(k.high for k in bars)),
            "low": int(min(k.low for k in bars)),
            "close": int(bars[-1].close),
        }

    meta = {
        "prev_day": ohlc(prev_day_kbars),
        "prev_night": ohlc(prev_night_kbars),
        "today_open": int(today_open_price),
        "embedded_levels": embedded_levels,
    }
    return prev_day_kbars, prev_night_kbars, today_kbars, meta


# ──────────────────────────────────────────────
# Visualization
# ──────────────────────────────────────────────

def _kbars_to_df(kbars: list[KBar]) -> pd.DataFrame:
    rows = [{
        "Date": k.time,
        "Open": k.open,
        "High": k.high,
        "Low": k.low,
        "Close": k.close,
        "Volume": k.volume,
    } for k in kbars]
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    df.set_index("Date", inplace=True)
    return df


def _color_by_methods(n: int) -> str:
    if n >= 3:
        return "#FF4444"  # red = strong confluence
    if n == 2:
        return "#FF8800"  # orange
    return "#4488FF"       # blue = single method


def plot_key_levels(
    prev_day_kbars: list[KBar],
    prev_night_kbars: list[KBar],
    today_kbars: list[KBar],
    levels: list[KeyLevel],
    meta: dict,
    output_path: str,
) -> None:
    all_kbars = prev_day_kbars + prev_night_kbars + today_kbars
    df = _kbars_to_df(all_kbars)

    fig, (ax_candle, ax_vol) = plt.subplots(
        2, 1, figsize=(20, 12), height_ratios=[4, 1],
        gridspec_kw={"hspace": 0.05},
    )

    # --- Candlestick ---
    dates = mdates.date2num(df.index.to_pydatetime())
    width = 0.002  # candle width in date units

    for i, (dt, row) in enumerate(zip(dates, df.itertuples())):
        o, h, lo, c = row.Open, row.High, row.Low, row.Close
        color = "#26A69A" if c >= o else "#EF5350"
        ax_candle.plot([dt, dt], [lo, h], color=color, linewidth=0.8)
        body_lo = min(o, c)
        body_hi = max(o, c)
        body_h = max(body_hi - body_lo, 0.5)
        rect = FancyBboxPatch(
            (dt - width / 2, body_lo), width, body_h,
            boxstyle="round,pad=0.0005",
            facecolor=color, edgecolor=color, linewidth=0.5,
        )
        ax_candle.add_patch(rect)

    # --- Session dividers ---
    for session_label, kbars, color in [
        ("Prev Day", prev_day_kbars, "#E0E0E0"),
        ("Prev Night", prev_night_kbars, "#E8E0F0"),
        ("Today OR", today_kbars, "#E0F0E0"),
    ]:
        if not kbars:
            continue
        t0 = mdates.date2num(kbars[0].time)
        t1 = mdates.date2num(kbars[-1].time)
        ax_candle.axvspan(t0, t1, alpha=0.15, color=color, zorder=0)
        ax_candle.text(
            (t0 + t1) / 2, ax_candle.get_ylim()[1] if ax_candle.get_ylim()[1] > 0 else 22400,
            session_label, ha="center", va="top", fontsize=9,
            color="#666666", fontweight="bold",
        )

    # --- Key level lines ---
    xmin = dates[0]
    xmax = dates[-1]

    for kl in levels:
        color = _color_by_methods(kl.num_methods)
        lw = 1.0 + min(kl.score / 3, 2.0)
        alpha = min(0.3 + kl.score / 10, 0.9)
        ax_candle.hlines(
            kl.price, xmin, xmax,
            colors=color, linewidth=lw, alpha=alpha,
            linestyles="--" if kl.num_methods == 1 else "-",
        )
        sources_str = ", ".join(kl.sources[:4])
        if len(kl.sources) > 4:
            sources_str += "..."
        ax_candle.text(
            xmax + 0.005, kl.price,
            f" {kl.price}  [s={kl.score:.1f}, {kl.num_methods}m, {kl.touch_count}t]\n {sources_str}",
            fontsize=7, color=color, va="center",
            fontweight="bold" if kl.num_methods >= 2 else "normal",
        )

    # --- Embedded levels (ground truth) as thin green lines ---
    for el in meta.get("embedded_levels", []):
        ax_candle.hlines(
            el, xmin, xmax, colors="#00CC00", linewidth=0.5,
            alpha=0.4, linestyles=":",
        )

    ax_candle.set_ylabel("Price", fontsize=11)
    ax_candle.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    ax_candle.tick_params(labelbottom=False)
    ax_candle.set_title(
        "Confluence Key Level Detection — Synthetic TAIEX Futures 5min",
        fontsize=14, fontweight="bold",
    )

    # Fix y-axis
    all_prices = [k.high for k in all_kbars] + [k.low for k in all_kbars]
    level_prices = [kl.price for kl in levels]
    y_min = min(min(all_prices), min(level_prices) if level_prices else 99999) - 30
    y_max = max(max(all_prices), max(level_prices) if level_prices else 0) + 30
    ax_candle.set_ylim(y_min, y_max)
    ax_candle.set_xlim(xmin - 0.01, xmax + 0.08)

    # Legend
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color="#FF4444", lw=2, label="3+ methods (strong)"),
        Line2D([0], [0], color="#FF8800", lw=1.5, label="2 methods"),
        Line2D([0], [0], color="#4488FF", lw=1, linestyle="--", label="1 method"),
        Line2D([0], [0], color="#00CC00", lw=0.5, linestyle=":", label="Embedded truth"),
    ]
    ax_candle.legend(handles=legend_elements, loc="upper left", fontsize=8)

    # --- Volume bars ---
    vol_colors = ["#26A69A" if r.Close >= r.Open else "#EF5350" for r in df.itertuples()]
    ax_vol.bar(dates, df["Volume"], width=width, color=vol_colors, alpha=0.7)
    ax_vol.set_ylabel("Volume", fontsize=11)
    ax_vol.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    ax_vol.set_xlim(xmin - 0.01, xmax + 0.08)
    plt.xticks(rotation=30)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\n📊 Chart saved to: {output_path}")
    plt.close()


def print_levels_table(levels: list[KeyLevel]) -> None:
    print("\n" + "=" * 90)
    print("  Confluence Key Levels (sorted by score)")
    print("=" * 90)
    print(f"  {'Price':>8}  {'Score':>7}  {'Methods':>7}  {'Touches':>7}  Sources")
    print("-" * 90)
    for kl in levels:
        sources = ", ".join(kl.sources)
        print(f"  {kl.price:>8}  {kl.score:>7.2f}  {kl.num_methods:>7}  {kl.touch_count:>7}  {sources}")
    print("=" * 90)
    print(f"  Total: {len(levels)} zones detected")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main() -> None:
    print("Generating synthetic TAIEX futures data...")
    prev_day_kbars, prev_night_kbars, today_kbars, meta = generate_test_data()

    print(f"  Prev Day:   {len(prev_day_kbars)} bars, "
          f"O={meta['prev_day']['open']} H={meta['prev_day']['high']} "
          f"L={meta['prev_day']['low']} C={meta['prev_day']['close']}")
    print(f"  Prev Night: {len(prev_night_kbars)} bars, "
          f"O={meta['prev_night']['open']} H={meta['prev_night']['high']} "
          f"L={meta['prev_night']['low']} C={meta['prev_night']['close']}")
    print(f"  Today Open: {meta['today_open']}")
    print(f"  Embedded S/R levels: {meta['embedded_levels']}")

    pd_ohlc = meta["prev_day"]
    pn_ohlc = meta["prev_night"]

    session = SessionData(
        prev_day_high=pd_ohlc["high"],
        prev_day_low=pd_ohlc["low"],
        prev_day_close=pd_ohlc["close"],
        prev_night_high=pn_ohlc["high"],
        prev_night_low=pn_ohlc["low"],
        prev_night_close=pn_ohlc["close"],
        today_open=meta["today_open"],
        or_range=max(pd_ohlc["high"] - pd_ohlc["low"], 50),
        prev_day_kbars=prev_day_kbars,
        prev_night_kbars=prev_night_kbars,
    )

    print("\nRunning confluence key level detection...")
    levels = find_confluence_levels(
        session,
        swing_period=10,
        cluster_tolerance=50,
        volume_bucket_size=10,
        zone_tolerance=50,
        round_scan_range=500,
        touch_weight=1.0,
    )

    print_levels_table(levels)

    output_dir = Path(__file__).resolve().parent.parent / "data" / "debug"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(output_dir / "key_levels_test.png")

    plot_key_levels(
        prev_day_kbars, prev_night_kbars, today_kbars,
        levels, meta, output_path,
    )

    print("\nDone! Open the PNG to inspect detected key levels.")


if __name__ == "__main__":
    main()
