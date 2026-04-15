"""Structural profit lock (replaces time+ratio PL).

Rules (long; short is mirrored):
- Pressure-free: def1 OR def3 (see below).
- Last N closed bars: no new high since entry (peak unchanged vs N bars ago).
- ATR(period): current bar's ATR is in the bottom quartile of the last N bar-end
  ATR values (competition rank <= atr_rank_max).
- Latest confirmed swing low pivot after entry (left+right); none → skip.
- One-bar arming: if gates hold, next bar with gates + reclaim vs swing → set
  TS to swing - buffer (only if tighter than current SL/TS; then max with KL TS).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class StructuralProfitLockParams:
    lookback_bars: int = 12
    atr_period: int = 14
    atr_rank_max: int = 3
    swing_left: int = 5
    swing_right: int = 5
    swing_stop_buffer: int = 10
    pressure_high_lookback: int = 20
    def3_price_pct: float = 0.005
    pressure_mode: str = "any"  # any / def1 / def3 / def4


def competition_rank(values: list[float], x: float) -> int:
    """1 = smallest (ties share rank by strict less-than count)."""
    return 1 + sum(1 for v in values if v < x)


def _entry_bar_index(kbars: list[Any], entry_time: datetime) -> int:
    for i, k in enumerate(kbars):
        if k.time >= entry_time:
            return i
    return 0


def _peak_high_entry_to(kbars: list[Any], entry_idx: int, end_idx: int) -> int:
    h = int(kbars[entry_idx].high)
    for j in range(entry_idx + 1, end_idx + 1):
        h = max(h, int(kbars[j].high))
    return h


def _peak_low_entry_to(kbars: list[Any], entry_idx: int, end_idx: int) -> int:
    lo = int(kbars[entry_idx].low)
    for j in range(entry_idx + 1, end_idx + 1):
        lo = min(lo, int(kbars[j].low))
    return lo


def no_new_high_last_n(
    kbars: list[Any], entry_idx: int, L: int, n: int,
) -> bool:
    if L - n < entry_idx or n < 1:
        return False
    peak_before = _peak_high_entry_to(kbars, entry_idx, L - n)
    peak_now = _peak_high_entry_to(kbars, entry_idx, L)
    return peak_now == peak_before


def no_new_low_last_n(
    kbars: list[Any], entry_idx: int, L: int, n: int,
) -> bool:
    if L - n < entry_idx or n < 1:
        return False
    peak_before = _peak_low_entry_to(kbars, entry_idx, L - n)
    peak_now = _peak_low_entry_to(kbars, entry_idx, L)
    return peak_now == peak_before


def pressure_long_def1(
    key_levels: list[int],
    recent_high: int,
    _atr: float,
    _p: StructuralProfitLockParams,
) -> bool:
    """Hard vacuum above: no upper KL at all."""
    above = [kl for kl in key_levels if kl > recent_high]
    return len(above) == 0


def pressure_long_def3(
    key_levels: list[int],
    close_px: int,
    _atr: float,
    p: StructuralProfitLockParams,
    threshold_ref_price: int,
) -> bool:
    """Distance from price to nearest upper KL."""
    up = [kl for kl in key_levels if kl > close_px]
    if not up:
        return True
    dist = min(up) - close_px
    thr = int(threshold_ref_price * p.def3_price_pct)
    return dist > thr


def pressure_long_def4(
    key_levels: list[int],
    close_px: int,
    _atr: float,
    p: StructuralProfitLockParams,
    threshold_ref_price: int,
) -> bool:
    """Distance from price to nearest lower KL."""
    down = [kl for kl in key_levels if kl < close_px]
    if not down:
        return True
    dist = close_px - max(down)
    thr = int(threshold_ref_price * p.def3_price_pct)
    return dist > thr


def pressure_short_def1(
    key_levels: list[int],
    recent_low: int,
    _atr: float,
    _p: StructuralProfitLockParams,
) -> bool:
    """Hard vacuum below: no lower KL at all."""
    below = [kl for kl in key_levels if kl < recent_low]
    return len(below) == 0


def pressure_short_def3(
    key_levels: list[int],
    close_px: int,
    _atr: float,
    p: StructuralProfitLockParams,
    threshold_ref_price: int,
) -> bool:
    down = [kl for kl in key_levels if kl < close_px]
    if not down:
        return True
    dist = close_px - max(down)
    thr = int(threshold_ref_price * p.def3_price_pct)
    return dist > thr


def pressure_short_def4(
    key_levels: list[int],
    close_px: int,
    _atr: float,
    p: StructuralProfitLockParams,
    threshold_ref_price: int,
) -> bool:
    """Distance from price to nearest upper KL."""
    up = [kl for kl in key_levels if kl > close_px]
    if not up:
        return True
    dist = min(up) - close_px
    thr = int(threshold_ref_price * p.def3_price_pct)
    return dist > thr


def _pressure_mode_ok(mode: str, d1: bool, d3: bool, d4: bool) -> bool:
    if mode == "def1":
        return d1
    if mode == "def3":
        return d3
    if mode == "def4":
        return d4
    return d1 or d3 or d4


def atr_rank_gate(
    atr_arr: list[float | None], L: int, p: StructuralProfitLockParams,
) -> bool:
    n = p.lookback_bars
    min_L = max(p.atr_period, n - 1)
    if min_L > L:
        return False
    lo = L - n + 1
    window: list[float] = []
    for i in range(lo, L + 1):
        v = atr_arr[i]
        if v is None:
            return False
        window.append(float(v))
    cur = window[-1]
    r = competition_rank(window, cur)
    return r <= p.atr_rank_max


def latest_confirmed_swing_low(
    kbars: list[Any], entry_idx: int, L: int, p: StructuralProfitLockParams,
) -> tuple[int, int] | None:
    """Return (bar_index, swing_low_price) for most recent valid pivot; else None."""
    left, right = p.swing_left, p.swing_right
    best: tuple[int, int] | None = None
    for m in range(entry_idx, L - right + 1):
        lo_i = m - left
        hi_i = m + right
        if lo_i < 0:
            continue
        mid_low = float(kbars[m].low)
        if any(
            j != m and float(kbars[j].low) <= mid_low
            for j in range(lo_i, hi_i + 1)
        ):
            continue
        best = (m, int(kbars[m].low))
    return best


def latest_confirmed_swing_high(
    kbars: list[Any], entry_idx: int, L: int, p: StructuralProfitLockParams,
) -> tuple[int, int] | None:
    left, right = p.swing_left, p.swing_right
    best: tuple[int, int] | None = None
    for m in range(entry_idx, L - right + 1):
        lo_i = m - left
        hi_i = m + right
        if lo_i < 0:
            continue
        mid_hi = float(kbars[m].high)
        if any(
            j != m and float(kbars[j].high) >= mid_hi
            for j in range(lo_i, hi_i + 1)
        ):
            continue
        best = (m, int(kbars[m].high))
    return best


def structural_gates_long(
    kbars: list[Any],
    entry_time: datetime,
    entry_price: int,
    key_levels: list[int],
    atr_arr: list[float | None],
    p: StructuralProfitLockParams,
) -> tuple[bool, int | None, int]:
    """Returns (gates_ok, swing_low_price_or_none, close_last)."""
    if not kbars:
        return False, None, 0
    L = len(kbars) - 1
    close_px = int(kbars[L].close)
    entry_idx = _entry_bar_index(kbars, entry_time)
    if not key_levels:
        return False, None, close_px
    atr_ref = atr_arr[L]
    if atr_ref is None:
        return False, None, close_px

    H = min(p.pressure_high_lookback, L - entry_idx + 1)
    if H < 1:
        return False, None, close_px
    from_h = L - H + 1
    recent_high = max(int(kbars[j].high) for j in range(from_h, L + 1))

    d1 = pressure_long_def1(key_levels, recent_high, float(atr_ref), p)
    threshold_ref_price = int(entry_price) if entry_price > 0 else close_px
    d3 = pressure_long_def3(
        key_levels, close_px, float(atr_ref), p, threshold_ref_price,
    )
    d4 = pressure_long_def4(
        key_levels, close_px, float(atr_ref), p, threshold_ref_price,
    )
    if not _pressure_mode_ok(p.pressure_mode, d1, d3, d4):
        return False, None, close_px

    if not atr_rank_gate(atr_arr, L, p):
        return False, None, close_px

    if not no_new_high_last_n(kbars, entry_idx, L, p.lookback_bars):
        return False, None, close_px

    piv = latest_confirmed_swing_low(kbars, entry_idx, L, p)
    if piv is None:
        return False, None, close_px
    _, swing_px = piv
    return True, swing_px, close_px


def structural_gates_short(
    kbars: list[Any],
    entry_time: datetime,
    entry_price: int,
    key_levels: list[int],
    atr_arr: list[float | None],
    p: StructuralProfitLockParams,
) -> tuple[bool, int | None, int]:
    if not kbars:
        return False, None, 0
    L = len(kbars) - 1
    close_px = int(kbars[L].close)
    entry_idx = _entry_bar_index(kbars, entry_time)
    if not key_levels:
        return False, None, close_px
    atr_ref = atr_arr[L]
    if atr_ref is None:
        return False, None, close_px

    H = min(p.pressure_high_lookback, L - entry_idx + 1)
    if H < 1:
        return False, None, close_px
    from_h = L - H + 1
    recent_low = min(int(kbars[j].low) for j in range(from_h, L + 1))

    d1 = pressure_short_def1(key_levels, recent_low, float(atr_ref), p)
    threshold_ref_price = int(entry_price) if entry_price > 0 else close_px
    d3 = pressure_short_def3(
        key_levels, close_px, float(atr_ref), p, threshold_ref_price,
    )
    d4 = pressure_short_def4(
        key_levels, close_px, float(atr_ref), p, threshold_ref_price,
    )
    if not _pressure_mode_ok(p.pressure_mode, d1, d3, d4):
        return False, None, close_px

    if not atr_rank_gate(atr_arr, L, p):
        return False, None, close_px

    if not no_new_low_last_n(kbars, entry_idx, L, p.lookback_bars):
        return False, None, close_px

    piv = latest_confirmed_swing_high(kbars, entry_idx, L, p)
    if piv is None:
        return False, None, close_px
    _, swing_px = piv
    return True, swing_px, close_px


def structural_debug_long(
    kbars: list[Any],
    entry_time: datetime,
    entry_price: int,
    key_levels: list[int],
    atr_arr: list[float | None],
    p: StructuralProfitLockParams,
) -> dict[str, Any]:
    """Return detailed gate evaluation for long PL diagnostics."""
    out: dict[str, Any] = {
        "ok": False,
        "close_px": None,
        "swing_px": None,
        "d1": False,
        "d3": False,
        "d4": False,
        "atr_rank_ok": False,
        "no_new_extreme_ok": False,
        "reason": "",
    }
    if not kbars:
        out["reason"] = "no_kbars"
        return out
    L = len(kbars) - 1
    close_px = int(kbars[L].close)
    out["close_px"] = close_px
    entry_idx = _entry_bar_index(kbars, entry_time)
    if not key_levels:
        out["reason"] = "no_key_levels"
        return out
    atr_ref = atr_arr[L]
    out["atr_ref"] = atr_ref
    if atr_ref is None:
        out["reason"] = "atr_unavailable"
        return out

    H = min(p.pressure_high_lookback, L - entry_idx + 1)
    if H < 1:
        out["reason"] = "insufficient_bars_for_pressure"
        return out
    from_h = L - H + 1
    recent_high = max(int(kbars[j].high) for j in range(from_h, L + 1))
    out["recent_high"] = recent_high

    threshold_ref_price = int(entry_price) if entry_price > 0 else close_px
    out["pressure_ref_price"] = threshold_ref_price
    d1 = pressure_long_def1(key_levels, recent_high, float(atr_ref), p)
    d3 = pressure_long_def3(
        key_levels, close_px, float(atr_ref), p, threshold_ref_price,
    )
    d4 = pressure_long_def4(
        key_levels, close_px, float(atr_ref), p, threshold_ref_price,
    )
    out["d1"], out["d3"], out["d4"] = d1, d3, d4
    if not _pressure_mode_ok(p.pressure_mode, d1, d3, d4):
        out["reason"] = "pressure_gate_false"
        return out

    atr_ok = atr_rank_gate(atr_arr, L, p)
    out["atr_rank_ok"] = atr_ok
    if not atr_ok:
        out["reason"] = "atr_rank_gate_false"
        return out

    nhex_ok = no_new_high_last_n(kbars, entry_idx, L, p.lookback_bars)
    out["no_new_extreme_ok"] = nhex_ok
    if not nhex_ok:
        out["reason"] = "no_new_high_gate_false"
        return out

    piv = latest_confirmed_swing_low(kbars, entry_idx, L, p)
    if piv is None:
        out["reason"] = "no_swing_pivot"
        return out
    _, swing_px = piv
    out["swing_px"] = swing_px
    out["ok"] = True
    out["reason"] = "ok"
    return out


def structural_debug_short(
    kbars: list[Any],
    entry_time: datetime,
    entry_price: int,
    key_levels: list[int],
    atr_arr: list[float | None],
    p: StructuralProfitLockParams,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "close_px": None,
        "swing_px": None,
        "d1": False,
        "d3": False,
        "d4": False,
        "atr_rank_ok": False,
        "no_new_extreme_ok": False,
        "reason": "",
    }
    if not kbars:
        out["reason"] = "no_kbars"
        return out
    L = len(kbars) - 1
    close_px = int(kbars[L].close)
    out["close_px"] = close_px
    entry_idx = _entry_bar_index(kbars, entry_time)
    if not key_levels:
        out["reason"] = "no_key_levels"
        return out
    atr_ref = atr_arr[L]
    out["atr_ref"] = atr_ref
    if atr_ref is None:
        out["reason"] = "atr_unavailable"
        return out

    H = min(p.pressure_high_lookback, L - entry_idx + 1)
    if H < 1:
        out["reason"] = "insufficient_bars_for_pressure"
        return out
    from_h = L - H + 1
    recent_low = min(int(kbars[j].low) for j in range(from_h, L + 1))
    out["recent_low"] = recent_low

    threshold_ref_price = int(entry_price) if entry_price > 0 else close_px
    out["pressure_ref_price"] = threshold_ref_price
    d1 = pressure_short_def1(key_levels, recent_low, float(atr_ref), p)
    d3 = pressure_short_def3(
        key_levels, close_px, float(atr_ref), p, threshold_ref_price,
    )
    d4 = pressure_short_def4(
        key_levels, close_px, float(atr_ref), p, threshold_ref_price,
    )
    out["d1"], out["d3"], out["d4"] = d1, d3, d4
    if not _pressure_mode_ok(p.pressure_mode, d1, d3, d4):
        out["reason"] = "pressure_gate_false"
        return out

    atr_ok = atr_rank_gate(atr_arr, L, p)
    out["atr_rank_ok"] = atr_ok
    if not atr_ok:
        out["reason"] = "atr_rank_gate_false"
        return out

    nlex_ok = no_new_low_last_n(kbars, entry_idx, L, p.lookback_bars)
    out["no_new_extreme_ok"] = nlex_ok
    if not nlex_ok:
        out["reason"] = "no_new_low_gate_false"
        return out

    piv = latest_confirmed_swing_high(kbars, entry_idx, L, p)
    if piv is None:
        out["reason"] = "no_swing_pivot"
        return out
    _, swing_px = piv
    out["swing_px"] = swing_px
    out["ok"] = True
    out["reason"] = "ok"
    return out
