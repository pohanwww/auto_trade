"""Confluence Key Level Detection System.

Combines 4 independent methods to find support/resistance levels,
merges nearby levels into zones, and scores them by confluence
(how many methods agree) and touch count (how many times the market
actually tested the level).

Methods:
1. Swing Cluster   – swing high/low reversal points, clustered
2. Volume Profile  – high-volume price nodes
3. Gap Analysis    – session gap boundaries
4. Round Numbers   – psychological price levels (x00, x500, x000)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auto_trade.models.market import KBar, KBarList


# ──────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────

@dataclass
class RawKeyLevel:
    """Single key level produced by one detector."""
    price: int
    weight: float
    method: str
    label: str = ""
    first_seen: datetime | None = None


@dataclass
class KeyLevel:
    """Merged zone with confluence score."""
    price: int
    score: float
    num_methods: int
    touch_count: int = 0
    first_seen: datetime | None = None
    last_touch: datetime | None = None
    sources: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────
# 1. Swing Cluster Detector
# ──────────────────────────────────────────────

def detect_swing_clusters(
    kbars: list[KBar],
    period: int = 10,
    cluster_tolerance: int = 50,
    debug: bool = False,
) -> list[RawKeyLevel]:
    """Find swing high/low points and cluster nearby ones.

    A swing high at bar[i] means bar[i].high is the highest among
    the surrounding ``period`` bars on each side. Swing low is the
    mirror. Nearby swing points within ``cluster_tolerance`` are
    grouped; the cluster's weight equals its touch count.
    """
    n = len(kbars)
    if debug:
        print(
            f"[SWING] start: bars={n}, period={period}, "
            f"cluster_tol={cluster_tolerance}",
        )
    if n < period * 2 + 1:
        if debug:
            print(
                f"[SWING] skip: bars too few (need >= {period * 2 + 1}, got {n})",
            )
        return []

    raw_swings: list[tuple[int, datetime]] = []  # (price, time)

    for i in range(period, n - period):
        h = int(kbars[i].high)
        lo = int(kbars[i].low)
        t = kbars[i].time

        is_swing_high = all(
            h >= int(kbars[j].high) for j in range(i - period, i)
        ) and all(
            h >= int(kbars[j].high) for j in range(i + 1, i + period + 1)
        )
        if is_swing_high:
            raw_swings.append((h, t))
            if debug:
                print(f"[SWING] high@i={i}: price={h}, time={t}")

        is_swing_low = all(
            lo <= int(kbars[j].low) for j in range(i - period, i)
        ) and all(
            lo <= int(kbars[j].low) for j in range(i + 1, i + period + 1)
        )
        if is_swing_low:
            raw_swings.append((lo, t))
            if debug:
                print(f"[SWING] low@i={i}: price={lo}, time={t}")

    if not raw_swings:
        if debug:
            print("[SWING] no raw swings found")
        return []

    raw_swings.sort(key=lambda x: x[0])
    if debug:
        print(f"[SWING] raw swings total={len(raw_swings)}")
    clusters: list[list[tuple[int, datetime]]] = [[raw_swings[0]]]
    for item in raw_swings[1:]:
        if item[0] - clusters[-1][0][0] <= cluster_tolerance:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    if debug:
        print(f"[SWING] clusters formed={len(clusters)}")

    results: list[RawKeyLevel] = []
    for idx, cluster in enumerate(clusters, 1):
        avg_price = sum(p for p, _ in cluster) // len(cluster)
        count = len(cluster)
        earliest = min(t for _, t in cluster)
        results.append(RawKeyLevel(
            price=avg_price,
            weight=3.0 * count,
            method="swing",
            label=f"swing({count})",
            first_seen=earliest,
        ))
        if debug:
            lo_p = min(p for p, _ in cluster)
            hi_p = max(p for p, _ in cluster)
            print(
                f"[SWING] cluster#{idx}: n={count}, range=[{lo_p},{hi_p}], "
                f"avg={avg_price}, weight={3.0 * count:.1f}",
            )
    if debug:
        print(f"[SWING] done: output_levels={len(results)}")
    return results


# ──────────────────────────────────────────────
# 2. Pivot Points Detector
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# 2. Volume Profile Detector
# ──────────────────────────────────────────────

def detect_volume_nodes(
    kbars: list[KBar],
    bucket_size: int = 10,
) -> list[RawKeyLevel]:
    """Find High Volume Nodes from a volume-at-price profile.

    Each K-bar's volume is distributed equally across all price buckets
    it spans (low to high). Buckets with volume > mean + 1 std are HVN.
    Skipped entirely if total volume is 0.
    """
    total_vol = sum(int(k.volume) for k in kbars)
    if total_vol == 0:
        return []

    buckets: dict[int, float] = {}
    for k in kbars:
        lo = int(k.low)
        hi = int(k.high)
        vol = int(k.volume)
        if vol == 0 or hi <= lo:
            continue
        lo_bucket = (lo // bucket_size) * bucket_size
        hi_bucket = (hi // bucket_size) * bucket_size
        num_buckets = max(1, (hi_bucket - lo_bucket) // bucket_size + 1)
        per_bucket = vol / num_buckets
        for b in range(lo_bucket, hi_bucket + bucket_size, bucket_size):
            buckets[b] = buckets.get(b, 0.0) + per_bucket

    if len(buckets) < 3:
        return []

    volumes = list(buckets.values())
    mean_vol = statistics.mean(volumes)
    std_vol = statistics.stdev(volumes) if len(volumes) > 1 else 0.0
    threshold = mean_vol + std_vol
    max_vol = max(volumes)

    results: list[RawKeyLevel] = []
    for bucket_price, vol in buckets.items():
        if vol >= threshold:
            center = bucket_price + bucket_size // 2
            raw_w = vol / max_vol if max_vol > 0 else 1.0
            weight = round(raw_w * 0.5, 2)
            results.append(RawKeyLevel(
                price=center,
                weight=weight,
                method="volume",
                label=f"vol({raw_w:.0%})",
            ))
    return results


# ──────────────────────────────────────────────
# 3. Gap Analysis Detector
# ──────────────────────────────────────────────

def detect_gap_levels(
    prev_day_close: int | None,
    prev_night_close: int | None,
    today_open: int | None,
    or_range: int = 1,
) -> list[RawKeyLevel]:
    """Detect session gap boundaries as key levels.

    A gap exists when the opening price differs from the previous
    session's close. Both edges of the gap are key levels.
    """
    if today_open is None:
        return []

    results: list[RawKeyLevel] = []
    safe_range = max(or_range, 1)

    for prev_close, tag in [
        (prev_day_close, "day_gap"),
        (prev_night_close, "night_gap"),
    ]:
        if prev_close is None:
            continue
        gap = abs(today_open - prev_close)
        if gap < 5:
            continue
        weight = min(gap / safe_range, 2.0)
        results.append(RawKeyLevel(
            price=prev_close,
            weight=round(weight, 2),
            method="gap",
            label=f"{tag}_close",
        ))
        results.append(RawKeyLevel(
            price=today_open,
            weight=round(weight, 2),
            method="gap",
            label=f"{tag}_open",
        ))
    return results


# ──────────────────────────────────────────────
# 4. Round Numbers Detector
# ──────────────────────────────────────────────

def detect_round_numbers(
    reference_price: int,
    scan_range: int = 500,
) -> list[RawKeyLevel]:
    """Detect psychological round-number levels near reference_price.

    Only emits x000 (weight 2.0) and x500 (weight 1.5).
    x00 levels are too noisy for futures with large price ranges.
    """
    lo = reference_price - scan_range
    hi = reference_price + scan_range
    base = (lo // 500) * 500

    results: list[RawKeyLevel] = []
    price = base
    while price <= hi:
        if lo <= price <= hi:
            if price % 1000 == 0:
                w, tag = 2.0, f"round({price})"
            elif price % 500 == 0:
                w, tag = 1.5, f"round({price})"
            else:
                price += 500
                continue
            results.append(RawKeyLevel(
                price=price, weight=w, method="round", label=tag,
            ))
        price += 500
    return results


# ──────────────────────────────────────────────
# 5. Session OHLC Detector
# ──────────────────────────────────────────────

def detect_session_ohlc(
    prev_day_high: int,
    prev_day_low: int,
    prev_day_close: int,
    prev_night_high: int | None = None,
    prev_night_low: int | None = None,
    prev_night_close: int | None = None,
) -> list[RawKeyLevel]:
    """Emit previous session OHLC extremes as key levels.

    These are the most basic S/R levels that every trader watches:
    the prior session's high, low, and close.
    """
    results: list[RawKeyLevel] = []
    if prev_day_high:
        results.append(RawKeyLevel(
            price=prev_day_high, weight=2.0,
            method="session", label="prev_day_H",
        ))
    if prev_day_low:
        results.append(RawKeyLevel(
            price=prev_day_low, weight=2.0,
            method="session", label="prev_day_L",
        ))
    if prev_day_close:
        results.append(RawKeyLevel(
            price=prev_day_close, weight=2.0,
            method="session", label="prev_day_C",
        ))
    if prev_night_high:
        results.append(RawKeyLevel(
            price=prev_night_high, weight=2.0,
            method="session", label="prev_night_H",
        ))
    if prev_night_low:
        results.append(RawKeyLevel(
            price=prev_night_low, weight=2.0,
            method="session", label="prev_night_L",
        ))
    if prev_night_close:
        results.append(RawKeyLevel(
            price=prev_night_close, weight=2.0,
            method="session", label="prev_night_C",
        ))
    return results


# ──────────────────────────────────────────────
# Touch Verification
# ──────────────────────────────────────────────

def count_touches_for_zones(
    zones: list[KeyLevel],
    kbars: list[KBar],
    period: int = 10,
    tolerance: int = 50,
) -> None:
    """Count swing touches near each zone, weighted by bounce magnitude."""
    n = len(kbars)
    if n < period * 2 + 1:
        return

    swings: list[tuple[int, float, datetime]] = []  # (price, bounce, time)

    for i in range(period, n - period):
        h = int(kbars[i].high)
        lo = int(kbars[i].low)
        t = kbars[i].time

        is_swing_high = all(
            h >= int(kbars[j].high) for j in range(i - period, i)
        ) and all(
            h >= int(kbars[j].high) for j in range(i + 1, i + period + 1)
        )
        if is_swing_high:
            left_low = min(int(kbars[j].low) for j in range(i - period, i))
            right_low = min(int(kbars[j].low) for j in range(i + 1, i + period + 1))
            bounce = h - min(left_low, right_low)
            swings.append((h, max(bounce, 1.0), t))

        is_swing_low = all(
            lo <= int(kbars[j].low) for j in range(i - period, i)
        ) and all(
            lo <= int(kbars[j].low) for j in range(i + 1, i + period + 1)
        )
        if is_swing_low:
            left_high = max(int(kbars[j].high) for j in range(i - period, i))
            right_high = max(int(kbars[j].high) for j in range(i + 1, i + period + 1))
            bounce = max(left_high, right_high) - lo
            swings.append((lo, max(bounce, 1.0), t))

    if not swings:
        return

    avg_bounce = sum(b for _, b, _ in swings) / len(swings)
    if avg_bounce <= 0:
        avg_bounce = 1.0

    for zone in zones:
        nearby = [(p, b, t) for p, b, t in swings if abs(p - zone.price) <= tolerance]
        if not nearby:
            continue
        touch_score = sum(b / avg_bounce for _, b, _ in nearby)
        zone.touch_count = round(touch_score)
        zone.last_touch = max(t for _, _, t in nearby)
        zone.sources.append(f"touch({len(nearby)},bounce={touch_score:.1f})")


# ──────────────────────────────────────────────
# Merge Engine
# ──────────────────────────────────────────────

def merge_to_zones(
    raw_levels: list[RawKeyLevel],
    zone_tolerance: int = 50,
) -> list[KeyLevel]:
    """Merge nearby raw levels into zones and compute base weight.

    For most methods, weights are summed directly.
    For 'volume' method only, take the max weight (no stacking).

    score = total_weight (sum of all raw level weights in the zone).
    """
    if not raw_levels:
        return []

    sorted_levels = sorted(raw_levels, key=lambda lv: lv.price)

    clusters: list[list[RawKeyLevel]] = [[sorted_levels[0]]]
    for lv in sorted_levels[1:]:
        centroid = sum(r.price for r in clusters[-1]) // len(clusters[-1])
        if abs(lv.price - centroid) <= zone_tolerance:
            clusters[-1].append(lv)
        else:
            clusters.append([lv])

    zones: list[KeyLevel] = []
    for cluster in clusters:
        best = max(cluster, key=lambda r: r.weight)
        total_weight = sum(r.weight for r in cluster)
        if total_weight == 0:
            total_weight = 0.01

        methods = set(r.method for r in cluster)
        sources = [r.label for r in cluster]
        times = [r.first_seen for r in cluster if r.first_seen is not None]
        earliest = min(times) if times else None

        zones.append(KeyLevel(
            price=best.price,
            score=round(total_weight, 2),
            num_methods=len(methods),
            first_seen=earliest,
            sources=sources,
        ))

    return zones



# ──────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────

@dataclass
class SessionData:
    """Input data for confluence detection."""
    prev_day_high: int = 0
    prev_day_low: int = 0
    prev_day_close: int = 0
    prev_night_high: int | None = None
    prev_night_low: int | None = None
    prev_night_close: int | None = None
    today_open: int | None = None
    or_range: int = 1
    or_high: int | None = None
    or_low: int | None = None
    or_kl_weight: float = 2.0
    prev_day_kbars: list[KBar] = field(default_factory=list)
    prev_night_kbars: list[KBar] = field(default_factory=list)


def find_confluence_levels(
    session: SessionData,
    *,
    swing_period: int = 10,
    cluster_tolerance: int = 50,
    volume_bucket_size: int = 10,
    zone_tolerance: int = 50,
    round_scan_range: int = 500,
    touch_weight: float = 1.0,
    max_levels: int = 10,
    recency_pool: int = 20,
    in_night_session: bool = False,
) -> list[KeyLevel]:
    """Run all detectors, merge into zones, count touches, and score.

    score = total_weight + touch_weight × touch_count

    Selection: sort by last_touch (recency), take top ``recency_pool``,
    then sort by score and return top ``max_levels``.
    """
    all_kbars = session.prev_day_kbars + session.prev_night_kbars
    raw: list[RawKeyLevel] = []

    # 1. Swing Cluster
    if all_kbars:
        raw.extend(detect_swing_clusters(
            all_kbars, period=swing_period, cluster_tolerance=cluster_tolerance,
        ))

    # 2. Volume Profile
    if all_kbars:
        raw.extend(detect_volume_nodes(
            all_kbars, bucket_size=volume_bucket_size,
        ))

    # 3. Round Numbers
    open_price = session.today_open or session.prev_day_close
    if open_price:
        raw.extend(detect_round_numbers(
            open_price, scan_range=round_scan_range,
        ))

    # 4. Session OHLC
    raw.extend(detect_session_ohlc(
        prev_day_high=session.prev_day_high,
        prev_day_low=session.prev_day_low,
        prev_day_close=session.prev_day_close,
        prev_night_high=session.prev_night_high,
        prev_night_low=session.prev_night_low,
        prev_night_close=session.prev_night_close,
    ))

    # 5. OR High / Low
    if session.or_high is not None:
        raw.append(RawKeyLevel(
            price=session.or_high, weight=session.or_kl_weight,
            method="session", label="OR_H",
        ))
    if session.or_low is not None:
        raw.append(RawKeyLevel(
            price=session.or_low, weight=session.or_kl_weight,
            method="session", label="OR_L",
        ))

    # Merge into zones
    zones = merge_to_zones(raw, zone_tolerance=zone_tolerance)

    # Touch verification
    if all_kbars:
        count_touches_for_zones(
            zones, all_kbars, period=swing_period, tolerance=zone_tolerance,
        )
        for zone in zones:
            zone.score = round(zone.score + touch_weight * zone.touch_count, 2)

    # --- Selection: recency first, then score ---
    epoch = datetime.min
    zones.sort(
        key=lambda z: (z.last_touch or z.first_seen or epoch, z.score),
        reverse=True,
    )
    recent = zones[:recency_pool]
    recent.sort(key=lambda z: z.score, reverse=True)
    return recent[:max_levels]


# ──────────────────────────────────────────────
# Shared high-level KL calculation (strategy + dashboard)
# ──────────────────────────────────────────────

EXCHANGE_DAY_START = time(8, 45)
EXCHANGE_DAY_END = time(13, 45)
EXCHANGE_NIGHT_START = time(15, 0)
EXCHANGE_NIGHT_END = time(5, 0)


@dataclass
class KLCalcResult:
    """Result container for calculate_key_levels_from_kbars."""
    levels: list[KeyLevel]
    day_ohlc: dict[str, int]
    night_ohlc: dict[str, int]
    today_open: int | None
    day_sessions: dict
    night_sessions: dict
    today_day_kbars: list
    today_night_kbars: list
    agg_day_kbars: list
    agg_night_kbars: list


def split_sessions(
    kbars: list[KBar],
    trading_day: date,
    in_night_session: bool = False,
) -> tuple[dict, dict, list, list]:
    """Split kbars into grouped day/night sessions.

    Canonical session-splitting logic shared between strategy and dashboard.
    In night mode, today's day kbars are also placed into day_sessions
    so they participate in KL calculation as "prev" data.

    Returns:
        day_sessions:  dict[date, list[KBar]] — historical (+ today's day for night mode)
        night_sessions: dict[date, list[KBar]] — historical night sessions
        today_day_kbars:  list[KBar] — today's day session (chart display)
        today_night_kbars: list[KBar] — tonight's session (chart display)
    """
    day_sessions: dict[date, list] = {}
    night_sessions: dict[date, list] = {}
    today_day: list = []
    today_night: list = []

    for kbar in kbars:
        d = kbar.time.date()
        t = kbar.time.time()

        if EXCHANGE_DAY_START <= t < EXCHANGE_DAY_END:
            if d < trading_day:
                day_sessions.setdefault(d, []).append(kbar)
            elif d == trading_day:
                today_day.append(kbar)
                if in_night_session:
                    day_sessions.setdefault(d, []).append(kbar)
        elif t >= EXCHANGE_NIGHT_START:
            if d < trading_day:
                night_sessions.setdefault(d, []).append(kbar)
            elif d == trading_day and in_night_session:
                today_night.append(kbar)
        elif t < EXCHANGE_NIGHT_END:
            ns_date = d - timedelta(days=1)
            if ns_date < trading_day:
                night_sessions.setdefault(ns_date, []).append(kbar)
            elif ns_date == trading_day and in_night_session:
                today_night.append(kbar)

    today_day.sort(key=lambda k: k.time)
    today_night.sort(key=lambda k: k.time)
    return day_sessions, night_sessions, today_day, today_night


def calculate_key_levels_from_kbars(
    kbars: list[KBar],
    trading_day: date,
    in_night_session: bool = False,
    *,
    or_range: int = 1,
    or_high: int | None = None,
    or_low: int | None = None,
    or_kl_weight: float = 2.0,
    swing_period: int = 10,
    cluster_tolerance: int = 50,
    zone_tolerance: int = 50,
    signal_level_count: int = 7,
    recency_pool: int = 20,
    session_lookback: int = 1,
) -> KLCalcResult:
    """Full KL calculation pipeline — shared by strategy and dashboard.

    1. Split kbars into day/night sessions
    2. Extract OHLC from latest session
    3. Aggregate N sessions of kbars for swing/volume detection
    4. Run find_confluence_levels → score sort → top 15
    """
    # 1. Session split
    day_sessions, night_sessions, today_day, today_night = split_sessions(
        kbars, trading_day, in_night_session,
    )

    # 2. OHLC from latest session
    day_ohlc: dict[str, int] = {}
    latest_day_kbars: list = []
    if day_sessions:
        latest = max(day_sessions.keys())
        latest_day_kbars = sorted(day_sessions[latest], key=lambda k: k.time)
        day_ohlc = {
            "high": int(max(k.high for k in latest_day_kbars)),
            "low": int(min(k.low for k in latest_day_kbars)),
            "close": int(latest_day_kbars[-1].close),
        }

    night_ohlc: dict[str, int] = {}
    latest_night_kbars: list = []
    if night_sessions:
        latest_n = max(night_sessions.keys())
        latest_night_kbars = sorted(night_sessions[latest_n], key=lambda k: k.time)
        night_ohlc = {
            "high": int(max(k.high for k in latest_night_kbars)),
            "low": int(min(k.low for k in latest_night_kbars)),
            "close": int(latest_night_kbars[-1].close),
        }

    # today_open from session's first bar
    today_session = today_night if in_night_session else today_day
    today_open = int(today_session[0].open) if today_session else None

    # 3. Aggregate kbars
    if session_lookback <= 1:
        agg_day = latest_day_kbars
        agg_night = latest_night_kbars
    else:
        d_dates = sorted(day_sessions.keys(), reverse=True)[:session_lookback]
        agg_day = []
        for dd in sorted(d_dates):
            agg_day.extend(sorted(day_sessions[dd], key=lambda k: k.time))
        n_dates = sorted(night_sessions.keys(), reverse=True)[:session_lookback]
        agg_night = []
        for nd in sorted(n_dates):
            agg_night.extend(sorted(night_sessions[nd], key=lambda k: k.time))

    session_data = SessionData(
        prev_day_high=day_ohlc.get("high", 0),
        prev_day_low=day_ohlc.get("low", 0),
        prev_day_close=day_ohlc.get("close", 0),
        prev_night_high=night_ohlc.get("high"),
        prev_night_low=night_ohlc.get("low"),
        prev_night_close=night_ohlc.get("close"),
        today_open=today_open,
        or_range=or_range,
        or_high=or_high,
        or_low=or_low,
        or_kl_weight=or_kl_weight,
        prev_day_kbars=agg_day,
        prev_night_kbars=agg_night,
    )

    # 4. Detect + score sort → top 15
    pool = find_confluence_levels(
        session_data,
        swing_period=swing_period,
        cluster_tolerance=cluster_tolerance,
        zone_tolerance=zone_tolerance,
        max_levels=recency_pool,
        recency_pool=recency_pool,
        in_night_session=in_night_session,
    )
    pool.sort(key=lambda z: z.score, reverse=True)
    levels = pool[:15]

    return KLCalcResult(
        levels=levels,
        day_ohlc=day_ohlc,
        night_ohlc=night_ohlc,
        today_open=today_open,
        day_sessions=day_sessions,
        night_sessions=night_sessions,
        today_day_kbars=today_day,
        today_night_kbars=today_night,
        agg_day_kbars=agg_day,
        agg_night_kbars=agg_night,
    )
