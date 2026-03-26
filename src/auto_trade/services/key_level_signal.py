"""Key Level Breakout / Bounce signal detection.

Given a K-bar, a list of KeyLevel objects, and current ATR, this module
decides whether a breakout or bounce has occurred at any key level.

Signal types
------------
- **breakout_long**  : close > level + atr * breakout_buffer
- **breakout_short** : close < level - atr * breakout_buffer
- **bounce_long**    : wick dips into zone but close stays above level
- **bounce_short**   : wick spikes into zone but close stays below level

Instant entry
-------------
If the bar's high/low penetrates a level by more than
``atr * instant_threshold`` *during* the bar, an ``instant`` flag is
set so the strategy can enter at that intra-bar price rather than
waiting for the bar to close.
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

        # --- Breakout long ---
        if close > level + buf_breakout:
            if prev_close is None or prev_close <= level + buf_breakout:
                instant = high > level + buf_instant and low <= level + buf_breakout
                entry = int(level + buf_instant) if instant else close
                signals.append(KeyLevelSignal(
                    signal_type="breakout_long",
                    key_level=kl,
                    entry_price=entry,
                    instant=instant,
                    score=kl.score,
                ))
                continue

        # --- Breakout short ---
        if close < level - buf_breakout:
            if prev_close is None or prev_close >= level - buf_breakout:
                instant = low < level - buf_instant and high >= level - buf_breakout
                entry = int(level - buf_instant) if instant else close
                signals.append(KeyLevelSignal(
                    signal_type="breakout_short",
                    key_level=kl,
                    entry_price=entry,
                    instant=instant,
                    score=kl.score,
                ))
                continue

        # --- Bounce long (support bounce) ---
        if low <= level + buf_bounce and close > level:
            signals.append(KeyLevelSignal(
                signal_type="bounce_long",
                key_level=kl,
                entry_price=close,
                score=kl.score,
            ))
            continue

        # --- Bounce short (resistance rejection) ---
        if high >= level - buf_bounce and close < level:
            signals.append(KeyLevelSignal(
                signal_type="bounce_short",
                key_level=kl,
                entry_price=close,
                score=kl.score,
            ))

    signals.sort(key=lambda s: s.score, reverse=True)
    return signals
