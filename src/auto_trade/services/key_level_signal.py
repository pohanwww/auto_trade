"""Key Level Breakout / Bounce signal detection.

Given a K-bar, a list of KeyLevel objects, and current ATR, this module
decides whether a breakout or bounce has occurred at any key level.

Signal types
------------
- **breakout_long**  : close > level + atr * breakout_buffer
- **breakout_short** : close < level - atr * breakout_buffer
- **bounce_long**    : prev was above level, wick dips into zone, close stays above
- **bounce_short**   : prev was below level, wick spikes into zone, close stays below

Instant entry
-------------
If the bar's high/low penetrates a level by more than
``atr * instant_threshold`` *during* the bar AND the bar started from
the other side (low/high was below/above the level), the signal fires
immediately without requiring close confirmation.  This mimics a
real-world buy/sell stop order at the threshold price.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auto_trade.models.market import KBar
    from auto_trade.services.key_level_detector import KeyLevel


@dataclass
class KeyLevelSignal:
    """A single detected signal at a key level."""

    signal_type: str  # breakout_long, breakout_short, bounce_long, bounce_short
    key_level: KeyLevel
    entry_price: int
    instant: bool = False  # True → can enter intra-bar
    score: float = 0.0  # inherited from the key level for ranking


def detect_signals(
    kbar: KBar,
    key_levels: list[KeyLevel],
    atr: float,
    prev_close: float | None = None,
    *,
    breakout_buffer: float = 0.2,
    bounce_buffer: float = 0.3,
    instant_threshold: float = 0.3,
    current_price: float | None = None,
) -> list[KeyLevelSignal]:
    """Scan *key_levels* for breakout / bounce signals on *kbar*.

    Parameters
    ----------
    kbar : KBar
        The current (closed or forming) K-bar.
    key_levels : list[KeyLevel]
        Key levels to check (already ranked by score).
    atr : float
        Current ATR value (used for buffer / threshold calculation).
    prev_close : float | None
        Previous bar's close.  When provided, a breakout requires the
        *previous* close to be on the other side of the level (avoids
        signalling when price has already been above/below for a while).
    breakout_buffer : float
        ATR multiplier for close-based breakout confirmation.
    bounce_buffer : float
        ATR multiplier defining the "touch zone" around a level.
    instant_threshold : float
        ATR multiplier – if intra-bar penetration exceeds this, flag
        instant entry.
    current_price : float | None
        Real-time price for instant breakout checks.  When provided,
        instant signals use this instead of bar high/low.
    """
    if atr <= 0:
        return []

    close = int(kbar.close)
    high = int(kbar.high)
    low = int(kbar.low)

    signals: list[KeyLevelSignal] = []

    for kl in key_levels:
        level = kl.price
        buf_breakout = atr * breakout_buffer
        buf_bounce = atr * bounce_buffer
        buf_instant = atr * instant_threshold

        # --- Instant breakout (uses real-time price when available) ---
        px = current_price if current_price is not None else None

        if px is not None:
            if px > level + buf_instant:
                if prev_close is None or prev_close <= level + buf_breakout:
                    signals.append(KeyLevelSignal(
                        signal_type="breakout_long",
                        key_level=kl,
                        entry_price=int(px),
                        instant=True,
                        score=kl.score,
                    ))
                    continue

            if px < level - buf_instant:
                if prev_close is None or prev_close >= level - buf_breakout:
                    signals.append(KeyLevelSignal(
                        signal_type="breakout_short",
                        key_level=kl,
                        entry_price=int(px),
                        instant=True,
                        score=kl.score,
                    ))
                    continue
        else:
            # Fallback: no real-time price, use bar OHLC
            if high > level + buf_instant and low <= level + buf_breakout and close >= level:
                if prev_close is None or prev_close <= level + buf_breakout:
                    signals.append(KeyLevelSignal(
                        signal_type="breakout_long",
                        key_level=kl,
                        entry_price=int(level + buf_instant),
                        instant=True,
                        score=kl.score,
                    ))
                    continue

            if low < level - buf_instant and high >= level - buf_breakout and close <= level:
                if prev_close is None or prev_close >= level - buf_breakout:
                    signals.append(KeyLevelSignal(
                        signal_type="breakout_short",
                        key_level=kl,
                        entry_price=int(level - buf_instant),
                        instant=True,
                        score=kl.score,
                    ))
                    continue

        # --- Close-confirmed breakout long (deferred entry) ---
        if close > level + buf_breakout:
            if prev_close is None or prev_close <= level + buf_breakout:
                signals.append(KeyLevelSignal(
                    signal_type="breakout_long",
                    key_level=kl,
                    entry_price=close,
                    instant=False,
                    score=kl.score,
                ))
                continue

        # --- Close-confirmed breakout short (deferred entry) ---
        if close < level - buf_breakout:
            if prev_close is None or prev_close >= level - buf_breakout:
                signals.append(KeyLevelSignal(
                    signal_type="breakout_short",
                    key_level=kl,
                    entry_price=close,
                    instant=False,
                    score=kl.score,
                ))
                continue

        # --- Bounce long (support bounce) ---
        # Requires prev_close was above level (price testing support from above,
        # NOT a weak breakout from below that didn't clear the buffer).
        if low <= level + buf_bounce and close > level:
            if prev_close is not None and prev_close > level:
                signals.append(KeyLevelSignal(
                    signal_type="bounce_long",
                    key_level=kl,
                    entry_price=close,
                    score=kl.score,
                ))
                continue

        # --- Bounce short (resistance rejection) ---
        # Requires prev_close was below level (price testing resistance from below).
        if high >= level - buf_bounce and close < level:
            if prev_close is not None and prev_close < level:
                signals.append(KeyLevelSignal(
                    signal_type="bounce_short",
                    key_level=kl,
                    entry_price=close,
                    score=kl.score,
                ))

    signals.sort(key=lambda s: s.score, reverse=True)
    return signals
