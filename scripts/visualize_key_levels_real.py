#!/usr/bin/env python3
"""Visualize Confluence Key Level Detection on REAL MXF (小台) data.

Fetches recent 5-min K-bars via Shioaji API, splits into
prev_day / prev_night / today sessions, runs the confluence
detector, and produces a candlestick chart with key level overlays.

Usage:
    cd <project_root>
    .venv/bin/python scripts/visualize_key_levels_real.py
    .venv/bin/python scripts/visualize_key_levels_real.py --days 5
    .venv/bin/python scripts/visualize_key_levels_real.py --date 2026-03-19
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl_config")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.models.market import KBar, KBarList
from auto_trade.services.key_level_detector import (
    KeyLevel,
    SessionData,
    find_confluence_levels,
)
from auto_trade.services.market_service import MarketService


# ──────────────────────────────────────────────
# Session splitting
# ──────────────────────────────────────────────

DAY_START = time(8, 45)
DAY_END = time(13, 45)
NIGHT_START = time(15, 0)
NIGHT_BOUNDARY = time(5, 0)


def split_sessions(
    kbar_list: KBarList, target_date: datetime,
) -> tuple[list[KBar], list[KBar], list[KBar]]:
    """Split K-bars into prev_day, prev_night, today_day sessions.

    Args:
        kbar_list: all fetched K-bars
        target_date: the "today" date for which we want key levels

    Returns:
        (prev_day_kbars, prev_night_kbars, today_day_kbars)
    """
    today = target_date.date()
    prev_day_kbars: list[KBar] = []
    prev_night_kbars: list[KBar] = []
    today_day_kbars: list[KBar] = []

    for kbar in kbar_list.kbars:
        d = kbar.time.date()
        t = kbar.time.time()

        if d == today and DAY_START <= t < DAY_END:
            today_day_kbars.append(kbar)
        elif DAY_START <= t < DAY_END and d < today:
            prev_day_kbars.append(kbar)
        elif t >= NIGHT_START and d < today:
            prev_night_kbars.append(kbar)
        elif t < NIGHT_BOUNDARY:
            ns_date = d - timedelta(days=1)
            if ns_date < today:
                prev_night_kbars.append(kbar)

    # Keep only the latest prev day session
    if prev_day_kbars:
        latest_day = max(k.time.date() for k in prev_day_kbars)
        prev_day_kbars = [k for k in prev_day_kbars if k.time.date() == latest_day]

    # Keep only the latest prev night session
    if prev_night_kbars:
        dates = set()
        for k in prev_night_kbars:
            t = k.time.time()
            if t >= NIGHT_START:
                dates.add(k.time.date())
            elif t < NIGHT_BOUNDARY:
                dates.add(k.time.date() - timedelta(days=1))
        if dates:
            latest_night = max(dates)
            filtered = []
            for k in prev_night_kbars:
                t = k.time.time()
                nd = k.time.date() if t >= NIGHT_START else k.time.date() - timedelta(days=1)
                if nd == latest_night:
                    filtered.append(k)
            prev_night_kbars = filtered

    prev_day_kbars.sort(key=lambda k: k.time)
    prev_night_kbars.sort(key=lambda k: k.time)
    today_day_kbars.sort(key=lambda k: k.time)

    return prev_day_kbars, prev_night_kbars, today_day_kbars


def compute_ohlc(kbars: list[KBar]) -> dict:
    if not kbars:
        return {"open": 0, "high": 0, "low": 0, "close": 0}
    return {
        "open": int(kbars[0].open),
        "high": int(max(k.high for k in kbars)),
        "low": int(min(k.low for k in kbars)),
        "close": int(kbars[-1].close),
    }


# ──────────────────────────────────────────────
# Visualization (reused from visualize_key_levels.py)
# ──────────────────────────────────────────────

def _kbars_to_df(kbars: list[KBar]) -> pd.DataFrame:
    rows = [{
        "Date": k.time, "Open": k.open, "High": k.high,
        "Low": k.low, "Close": k.close, "Volume": k.volume,
    } for k in kbars]
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    df.set_index("Date", inplace=True)
    return df


def _color_by_score(score: float) -> str:
    if score >= 15:
        return "#FF4444"
    if score >= 10:
        return "#FF8800"
    if score >= 5:
        return "#4488FF"
    return "#AAAAAA"


def plot_key_levels(
    prev_day_kbars: list[KBar],
    prev_night_kbars: list[KBar],
    today_kbars: list[KBar],
    levels: list[KeyLevel],
    output_path: str,
    title_suffix: str = "",
) -> None:
    all_kbars = prev_day_kbars + prev_night_kbars + today_kbars
    if not all_kbars:
        print("No K-bars to plot!")
        return

    df = _kbars_to_df(all_kbars)

    fig, (ax_candle, ax_vol) = plt.subplots(
        2, 1, figsize=(22, 13), height_ratios=[4, 1],
        gridspec_kw={"hspace": 0.05},
    )

    dates = mdates.date2num(df.index.to_pydatetime())
    width = 0.002

    for dt, row in zip(dates, df.itertuples()):
        o, h, lo, c = row.Open, row.High, row.Low, row.Close
        color = "#26A69A" if c >= o else "#EF5350"
        ax_candle.plot([dt, dt], [lo, h], color=color, linewidth=0.8)
        body_lo = min(o, c)
        body_hi = max(o, c)
        body_h = max(body_hi - body_lo, 0.3)
        rect = FancyBboxPatch(
            (dt - width / 2, body_lo), width, body_h,
            boxstyle="round,pad=0.0005",
            facecolor=color, edgecolor=color, linewidth=0.5,
        )
        ax_candle.add_patch(rect)

    # Session backgrounds
    for label, kbars, bg_color in [
        ("Prev Day", prev_day_kbars, "#E0E0E0"),
        ("Prev Night", prev_night_kbars, "#E8E0F0"),
        ("Today", today_kbars, "#E0F0E0"),
    ]:
        if not kbars:
            continue
        t0 = mdates.date2num(kbars[0].time)
        t1 = mdates.date2num(kbars[-1].time)
        ax_candle.axvspan(t0, t1, alpha=0.12, color=bg_color, zorder=0)
        mid_y = (ax_candle.get_ylim()[0] + ax_candle.get_ylim()[1]) / 2 if ax_candle.get_ylim()[1] > 0 else 0
        ax_candle.text(
            (t0 + t1) / 2, ax_candle.get_ylim()[1] if ax_candle.get_ylim()[1] > 0 else mid_y,
            label, ha="center", va="top", fontsize=9,
            color="#666666", fontweight="bold",
        )

    # Key level lines
    xmin, xmax = dates[0], dates[-1]

    for kl in levels:
        color = _color_by_score(kl.score)
        lw = 1.0
        alpha = min(0.3 + kl.score / 15, 0.9)
        is_strong = kl.score >= 5
        line_start = mdates.date2num(kl.first_seen) if kl.first_seen else xmin
        line_start = max(line_start, xmin)
        ax_candle.hlines(
            kl.price, line_start, xmax,
            colors=color, linewidth=lw, alpha=alpha,
            linestyles="-" if is_strong else "--",
        )
        sources_short = ", ".join(kl.sources[:4])
        if len(kl.sources) > 4:
            sources_short += "..."
        ax_candle.text(
            xmax + 0.003, kl.price,
            f" {kl.price}  [s={kl.score:.1f}, {kl.touch_count}t]\n {sources_short}",
            fontsize=6.5, color=color, va="center",
            fontweight="bold" if is_strong else "normal",
        )

    ax_candle.set_ylabel("Price", fontsize=11)
    ax_candle.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    ax_candle.tick_params(labelbottom=False)
    ax_candle.set_title(
        f"Confluence Key Level Detection — MXF 5min{title_suffix}",
        fontsize=14, fontweight="bold",
    )

    all_prices = [k.high for k in all_kbars] + [k.low for k in all_kbars]
    level_prices = [kl.price for kl in levels] if levels else []
    combined = all_prices + level_prices
    y_min = min(combined) - 30
    y_max = max(combined) + 30
    ax_candle.set_ylim(y_min, y_max)
    ax_candle.set_xlim(xmin - 0.01, xmax + 0.08)

    legend_elements = [
        Line2D([0], [0], color="#FF4444", lw=2, label="score ≥ 15 (strong)"),
        Line2D([0], [0], color="#FF8800", lw=1.5, label="score ≥ 10 (medium)"),
        Line2D([0], [0], color="#4488FF", lw=1, label="score ≥ 5 (weak)"),
        Line2D([0], [0], color="#AAAAAA", lw=1, linestyle="--", label="score < 5"),
    ]
    ax_candle.legend(handles=legend_elements, loc="upper left", fontsize=8)

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
    print("\n" + "=" * 95)
    print("  Confluence Key Levels (sorted by score)")
    print("=" * 95)
    print(f"  {'Price':>8}  {'Score':>7}  {'Touches':>7}  Sources")
    print("-" * 95)
    for kl in levels:
        sources = ", ".join(kl.sources)
        print(f"  {kl.price:>8}  {kl.score:>7.02f}  {kl.touch_count:>7}  {sources}")
    print("=" * 95)
    print(f"  Total: {len(levels)} zones detected")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize key levels on real MXF data")
    parser.add_argument("--days", type=int, default=3,
                        help="How many days of data to fetch (default: 3)")
    parser.add_argument("--date", type=str, default=None,
                        help="Target date for 'today' in YYYY-MM-DD (default: latest trading day)")
    parser.add_argument("--symbol", type=str, default="MXF")
    parser.add_argument("--sub-symbol", type=str, default="MXFR1")
    parser.add_argument("--timeframe", type=str, default="5m")
    args = parser.parse_args()

    print("🔌 Connecting to Shioaji API...")
    config = Config()
    api_client = create_api_client(
        config.api_key,
        config.secret_key,
        config.ca_cert_path,
        config.ca_password,
        simulation=True,
    )

    market_service = MarketService(api_client)

    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days + 2)

    print(f"📊 Fetching {args.symbol}/{args.sub_symbol} {args.timeframe} K-bars "
          f"from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...")

    kbar_list = market_service.get_futures_kbars_by_date_range(
        symbol=args.symbol,
        sub_symbol=args.sub_symbol,
        start_date=start_date,
        end_date=end_date,
        timeframe=args.timeframe,
    )
    print(f"  Fetched {len(kbar_list)} bars total")

    if len(kbar_list) == 0:
        print("❌ No data fetched. Check API credentials / market hours.")
        api_client.logout()
        return

    # Determine target date
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        day_dates = set()
        for k in kbar_list.kbars:
            if DAY_START <= k.time.time() < DAY_END:
                day_dates.add(k.time.date())
        if day_dates:
            target_date = datetime.combine(max(day_dates), time(8, 45))
        else:
            target_date = datetime.combine(kbar_list.kbars[-1].time.date(), time(8, 45))

    print(f"  Target date (today): {target_date.strftime('%Y-%m-%d')}")

    prev_day_kbars, prev_night_kbars, today_kbars = split_sessions(kbar_list, target_date)

    print(f"  Prev Day:   {len(prev_day_kbars)} bars")
    print(f"  Prev Night: {len(prev_night_kbars)} bars")
    print(f"  Today:      {len(today_kbars)} bars")

    pd_ohlc = compute_ohlc(prev_day_kbars)
    pn_ohlc = compute_ohlc(prev_night_kbars)
    today_open = int(today_kbars[0].open) if today_kbars else None

    if pd_ohlc["high"] == 0 and pn_ohlc["high"] == 0:
        print("❌ Not enough session data for key level detection.")
        api_client.logout()
        return

    print(f"  Prev Day  OHLC: O={pd_ohlc['open']} H={pd_ohlc['high']} "
          f"L={pd_ohlc['low']} C={pd_ohlc['close']}")
    print(f"  Prev Night OHLC: O={pn_ohlc['open']} H={pn_ohlc['high']} "
          f"L={pn_ohlc['low']} C={pn_ohlc['close']}")
    print(f"  Today Open: {today_open}")

    or_range = max(pd_ohlc["high"] - pd_ohlc["low"], 50)

    session = SessionData(
        prev_day_high=pd_ohlc["high"],
        prev_day_low=pd_ohlc["low"],
        prev_day_close=pd_ohlc["close"],
        prev_night_high=pn_ohlc["high"] if pn_ohlc["high"] else None,
        prev_night_low=pn_ohlc["low"] if pn_ohlc["low"] else None,
        prev_night_close=pn_ohlc["close"] if pn_ohlc["close"] else None,
        today_open=today_open,
        or_range=or_range,
        prev_day_kbars=prev_day_kbars,
        prev_night_kbars=prev_night_kbars,
    )

    print("\n🔍 Running confluence key level detection...")
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
    date_str = target_date.strftime("%Y%m%d")
    output_path = str(output_dir / f"key_levels_real_{date_str}.png")

    plot_key_levels(
        prev_day_kbars, prev_night_kbars, today_kbars,
        levels, output_path,
        title_suffix=f" — {target_date.strftime('%Y-%m-%d')}",
    )

    print("\n🔌 Logging out...")
    try:
        api_client.logout()
    except Exception:
        pass
    print("✅ Done!")


if __name__ == "__main__":
    main()
