#!/usr/bin/env python3
"""Find consolidation periods in MXF historical data.

Consolidation = net return ≈ 0%, regardless of intra-period volatility.
"""

from __future__ import annotations

from datetime import datetime

from auto_trade.core.config import Config
from auto_trade.core.client import create_api_client
from auto_trade.services.market_service import MarketService


def main():
    config = Config()
    api_client = create_api_client(
        config.api_key, config.secret_key,
        config.ca_cert_path, config.ca_password,
        simulation=True,
    )
    market_service = MarketService(api_client)

    start = datetime(2022, 1, 1)
    end = datetime(2026, 3, 25)

    print(f"Fetching 1m data for MXF from {start.date()} to {end.date()}...")

    kbar_list = market_service.get_futures_kbars_by_date_range(
        symbol=config.symbol,
        sub_symbol=config.sub_symbol,
        start_date=start,
        end_date=end,
        timeframe="1m",
    )

    bars = kbar_list.kbars
    if not bars:
        print("No data returned.")
        return

    print(f"Got {len(bars)} bars from {bars[0].time} to {bars[-1].time}\n")

    monthly: dict[str, list] = {}
    for bar in bars:
        key = bar.time.strftime("%Y-%m")
        monthly.setdefault(key, []).append(bar)

    months = []
    for key in sorted(monthly.keys()):
        mb = monthly[key]
        op = mb[0].open
        cp = mb[-1].close
        hp = max(b.high for b in mb)
        lp = min(b.low for b in mb)
        months.append({"key": key, "open": op, "close": cp, "high": hp, "low": lp})

    print("=" * 100)
    print("  MONTHLY MARKET CHARACTER")
    print("=" * 100)
    print(f"  {'Month':<10} {'Open':>8} {'Close':>8} {'High':>8} {'Low':>8} "
          f"{'Return':>8} {'Range':>8}  Character")
    print(f"  {'-' * 90}")

    for m in months:
        op, cp, hp, lp = m["open"], m["close"], m["high"], m["low"]
        ret = (cp - op) / op * 100
        rng = (hp - lp) / op * 100

        if abs(ret) < 3:
            char = "🟢 CONSOLIDATION"
        elif abs(ret) < 5:
            char = "🟡 MILD"
        elif ret > 5:
            char = "🔴 BULL"
        else:
            char = "🔵 BEAR"

        print(f"  {m['key']:<10} {op:>8.0f} {cp:>8.0f} {hp:>8.0f} {lp:>8.0f} "
              f"{ret:>+7.1f}% {rng:>7.1f}%  {char}")

    for window_size in [2, 3, 4, 6]:
        print(f"\n{'=' * 100}")
        print(f"  {window_size}-MONTH WINDOWS — sorted by |return| (smallest = most consolidation)")
        print("=" * 100)
        results = []
        for i in range(len(months) - window_size + 1):
            window = months[i:i + window_size]
            op = window[0]["open"]
            cp = window[-1]["close"]
            hp = max(w["high"] for w in window)
            lp = min(w["low"] for w in window)
            ret = (cp - op) / op * 100
            rng = (hp - lp) / op * 100
            sk = window[0]["key"]
            ek = window[-1]["key"]
            results.append((abs(ret), rng, ret, sk, ek, op, cp, hp, lp))

        results.sort(key=lambda x: x[0])
        for abs_ret, rng, ret, sk, ek, op, cp, hp, lp in results[:15]:
            tag = "⭐" if abs_ret < 3 else "  "
            print(f"  {tag} {sk} ~ {ek}  Open={op:>6.0f}  Close={cp:>6.0f}  "
                  f"H={hp:>6.0f}  L={lp:>6.0f}  "
                  f"Return={ret:>+6.1f}%  Range={rng:>5.1f}%")


if __name__ == "__main__":
    main()
