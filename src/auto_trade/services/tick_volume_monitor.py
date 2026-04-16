"""Tick-level volume helpers: rolling window vs baseline from closed 5m bars.

Taiwan futures ticks expose cumulative session volume (e.g. TickFOPv1.total_volume).
Incremental per-tick volume is computed as non-negative deltas between consecutive
ticks; a sudden drop in cumulative volume is treated as a session/reset boundary.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from auto_trade.models.market import KBarList


def avg_volume_per_seconds_from_last_n_closed_5m(
    kbar_list_5m: KBarList,
    *,
    n_closed_bars: int = 3,
    baseline_window_sec: int = 10,
    exclude_forming: bool = True,
) -> float | None:
    """Average traded volume per `baseline_window_sec` using the last N *closed* 5m bars.

    Excludes the latest bar when `exclude_forming` is True (assumes `kbars[-1]` is the
    currently forming 5m candle, matching live polling patterns).

    Example: N=3 → 15 minutes of volume; 15*60 / baseline_window_sec buckets.
    """
    if n_closed_bars < 1 or baseline_window_sec < 1:
        return None
    bars = kbar_list_5m.kbars
    if len(bars) < n_closed_bars + (1 if exclude_forming else 0):
        return None
    end = len(bars) - 1 if exclude_forming else len(bars)
    segment = bars[end - n_closed_bars : end]
    total_vol = sum(int(b.volume) for b in segment)
    if total_vol <= 0:
        return 0.0
    span_sec = n_closed_bars * 5 * 60
    num_buckets = span_sec / float(baseline_window_sec)
    if num_buckets <= 0:
        return None
    return total_vol / num_buckets


@dataclass
class RollingTickVolumeWindow:
    """Sum incremental tick volume over the last `window_sec` (wall-clock)."""

    window_sec: float = 10.0
    _events: deque[tuple[datetime, int]] = field(default_factory=deque)
    _last_cum: int | None = None

    def on_tick(self, tick_time: datetime, cumulative_volume: int) -> int:
        """Record one tick; return incremental volume for this tick (>= 0)."""
        if self._last_cum is not None and cumulative_volume < self._last_cum:
            msg = (
                "[TickVolume] Cumulative volume decreased (session/contract reset?): "
                f"prev={self._last_cum} curr={cumulative_volume} "
                "(incremental baseline reset for this tick)"
            )
            logger.warning("%s", msg)
            print(msg, flush=True)
        inc = _incremental_volume(self._last_cum, cumulative_volume)
        self._last_cum = cumulative_volume
        if inc > 0:
            self._events.append((tick_time, inc))
        self._trim(tick_time)
        return inc

    def _trim(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.window_sec)
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def rolling_sum(self, now: datetime | None = None) -> int:
        """Total incremental volume in the last `window_sec` ending at `now`."""
        if now is None:
            now = self._events[-1][0] if self._events else datetime.now()
        cutoff = now - timedelta(seconds=self.window_sec)
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()
        return sum(v for _, v in self._events)

    def reset_session(self) -> None:
        """Call after session break if cumulative volume resets."""
        self._events.clear()
        self._last_cum = None


def _incremental_volume(prev: int | None, cum: int) -> int:
    if prev is None:
        return max(0, cum)
    if cum < prev:
        # Session reset or contract roll — treat this tick's cum as new baseline.
        return max(0, cum)
    return cum - prev


def is_high_volume_vs_baseline(
    rolling_sum: float,
    baseline_per_window: float,
    *,
    multiplier: float = 1.5,
) -> bool:
    if baseline_per_window <= 0:
        return False
    return rolling_sum > multiplier * baseline_per_window
