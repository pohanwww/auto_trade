"""Confluence Key Level Detection System.

Combines 5 independent methods to find support/resistance levels,
merges nearby levels into zones, and scores them by confluence
(how many methods agree) and touch count (how many times the market
actually tested the level).

Methods:
1. Swing Cluster   – swing high/low reversal points, clustered
2. Pivot Points    – classic pivot from previous session OHLC
3. Volume Profile  – high-volume price nodes
4. Gap Analysis    – session gap boundaries
5. Round Numbers   – psychological price levels (x00, x500, x000)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auto_trade.models.market import KBar


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
) -> list[RawKeyLevel]:
    """Find swing high/low points and cluster nearby ones.

    A swing high at bar[i] means bar[i].high is the highest among
    the surrounding ``period`` bars on each side. Swing low is the
    mirror. Nearby swing points within ``cluster_tolerance`` are
    grouped; the cluster's weight equals its touch count.
    """
    n = len(kbars)
    if n < period * 2 + 1:
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

        is_swing_low = all(
            lo <= int(kbars[j].low) for j in range(i - period, i)
        ) and all(
            lo <= int(kbars[j].low) for j in range(i + 1, i + period + 1)
        )
        if is_swing_low:
            raw_swings.append((lo, t))

    if not raw_swings:
        return []

    raw_swings.sort(key=lambda x: x[0])
    clusters: list[list[tuple[int, datetime]]] = [[raw_swings[0]]]
    for item in raw_swings[1:]:
        if item[0] - clusters[-1][0][0] <= cluster_tolerance:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    results: list[RawKeyLevel] = []
    for cluster in clusters:
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
    return results


# ──────────────────────────────────────────────
# 2. Pivot Points Detector
# ──────────────────────────────────────────────

def detect_pivot_points(
    prev_high: int,
    prev_low: int,
    prev_close: int,
) -> list[RawKeyLevel]:
    """Classic pivot points from previous session OHLC.

    PP  = (H + L + C) / 3
    R1  = 2*PP - L,  S1 = 2*PP - H
    R2  = PP + (H-L), S2 = PP - (H-L)
    """
    pp = (prev_high + prev_low + prev_close) // 3
    r1 = 2 * pp - prev_low
    s1 = 2 * pp - prev_high
    r2 = pp + (prev_high - prev_low)
    s2 = pp - (prev_high - prev_low)

    levels = [
        (pp, "PP"),
        (r1, "R1"), (s1, "S1"),
        (r2, "R2"), (s2, "S2"),
    ]
    return [
        RawKeyLevel(price=price, weight=1.0, method="pivot", label=f"pivot({tag})")
        for price, tag in levels
    ]


# ──────────────────────────────────────────────
# 3. Volume Profile Detector
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
# 4. Gap Analysis Detector
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
# 5. Round Numbers Detector
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
# 6. Session OHLC Detector
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
) -> list[KeyLevel]:
    """Run all detectors, merge into zones, count touches, and score.

    score = total_weight + touch_weight × touch_count

    Returns up to ``max_levels`` KeyLevels, sorted by last_touch
    (most recent first), then by score descending as tiebreaker.
    """
    all_kbars = session.prev_day_kbars + session.prev_night_kbars
    raw: list[RawKeyLevel] = []

    # 1. Swing Cluster
    if all_kbars:
        raw.extend(detect_swing_clusters(
            all_kbars, period=swing_period, cluster_tolerance=cluster_tolerance,
        ))

    # 2. Pivot Points
    if session.prev_day_high and session.prev_day_low:
        raw.extend(detect_pivot_points(
            session.prev_day_high, session.prev_day_low, session.prev_day_close,
        ))

    # 3. Volume Profile
    if all_kbars:
        raw.extend(detect_volume_nodes(
            all_kbars, bucket_size=volume_bucket_size,
        ))

    # 4. Round Numbers
    ref_price = session.today_open or session.prev_day_close
    if ref_price:
        raw.extend(detect_round_numbers(
            ref_price, scan_range=round_scan_range,
        ))

    # 6. Session OHLC
    raw.extend(detect_session_ohlc(
        prev_day_high=session.prev_day_high,
        prev_day_low=session.prev_day_low,
        prev_day_close=session.prev_day_close,
        prev_night_high=session.prev_night_high,
        prev_night_low=session.prev_night_low,
        prev_night_close=session.prev_night_close,
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

    epoch = datetime.min
    zones.sort(
        key=lambda z: (z.last_touch or z.first_seen or epoch, z.score),
        reverse=True,
    )
    recent = zones[:15]
    recent.sort(key=lambda z: z.score, reverse=True)
    return recent[:max_levels]
