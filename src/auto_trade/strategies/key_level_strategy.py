"""Key Level Breakout / Bounce Strategy.

Intraday strategy for Taiwan futures that uses confluence key levels
detected from previous day + night sessions to generate breakout and
bounce entry signals.

=== Two operating modes ===
1. **OR mode** (``use_or=True``): Calculate Opening Range first, use it
   as a trend filter (only long above OR_High, only short below OR_Low),
   then enter on key-level breakout/bounce in the filtered direction.
2. **Pure mode** (``use_or=False``): Trade any key-level breakout/bounce
   without an OR filter.

=== Key level usage ===
- Top N levels (by score) → signal levels for breakout/bounce entry
- Remaining levels → trailing stop ladder passed to PositionManager

=== Entry logic ===
- Close-based breakout with ATR buffer
- Bounce: wick touches level but close stays on original side
- Instant entry: if intra-bar penetration exceeds ATR threshold

=== Exit ===
All exit logic is handled by PositionManager via metadata:
  override_stop_loss_price, override_take_profit_price,
  override_trailing_stop_points, key_levels, key_level_buffer
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from auto_trade.models.market import KBar, KBarList
from auto_trade.models.strategy import SignalType, StrategySignal
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.services.key_level_detector import (
    KeyLevel,
    SessionData,
    find_confluence_levels,
)
from auto_trade.services.key_level_signal import detect_signals
from auto_trade.strategies.base_strategy import BaseStrategy


def _log(msg: str, *args) -> None:
    """Print with [KL] prefix for easy grep."""
    text = msg % args if args else msg
    print(f"[KL] {text}")


class KeyLevelStrategy(BaseStrategy):

    _TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}

    def __init__(
        self,
        indicator_service: IndicatorService,
        # --- OR parameters ---
        use_or: bool = True,
        or_bars: int = 3,
        or_start_time: str = "08:45",
        entry_end_time: str = "12:30",
        session_end_time: str = "13:45",
        # --- Key level detection ---
        swing_period: int = 10,
        cluster_tolerance: int = 50,
        zone_tolerance: int = 50,
        signal_level_count: int = 5,
        # --- Signal detection ---
        breakout_buffer: float = 0.2,
        bounce_buffer: float = 0.3,
        instant_threshold: float = 0.3,
        atr_period: int = 14,
        # --- Direction ---
        long_only: bool = False,
        short_only: bool = False,
        # --- Risk ---
        max_trades_per_day: int = 1,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 2.0,
        key_level_buffer: float = 0.15,
        key_level_trail_mode: str = "current",  # "current" or "previous"
        # --- Entry types ---
        use_breakout: bool = True,
        use_bounce: bool = True,
        # --- Timeframe ---
        timeframe: str = "5m",
        **kwargs,
    ):
        super().__init__(indicator_service, name="KeyLevel Strategy")

        self.is_live = False

        # Timeframe → how many previous sessions to aggregate for key levels
        self.timeframe = timeframe
        tf_min = self._TF_MINUTES.get(timeframe, 5)
        self._session_lookback = max(1, tf_min // 5)

        # OR
        self.use_or = use_or
        self.or_bars = or_bars
        self.or_start_time = _parse_time(or_start_time)
        self.entry_end_time = _parse_time(entry_end_time)
        self.session_end_time = _parse_time(session_end_time)
        self._crosses_midnight = self.session_end_time <= self.or_start_time

        # Key level detection
        self.swing_period = swing_period
        self.cluster_tolerance = cluster_tolerance
        self.zone_tolerance = zone_tolerance
        self.signal_level_count = signal_level_count

        # Signal detection
        self.breakout_buffer = breakout_buffer
        self.bounce_buffer = bounce_buffer
        self.instant_threshold = instant_threshold
        self.atr_period = atr_period

        # Direction
        self.long_only = long_only
        self.short_only = short_only

        # Risk
        self.max_trades_per_day = max_trades_per_day
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier
        self.key_level_buffer = key_level_buffer
        self.key_level_trail_mode = key_level_trail_mode

        # Entry types
        self.use_breakout = use_breakout
        self.use_bounce = use_bounce

        _log(
            "=== KeyLevelStrategy initialized ===\n"
            "  mode=%s | timeframe=%s | session_lookback=%d\n"
            "  OR: use=%s, bars=%d, start=%s, entry_end=%s, session_end=%s\n"
            "  KL detect: swing=%d, cluster_tol=%d, zone_tol=%d, signal_count=%d\n"
            "  Signal: brk_buf=%.2f, bnc_buf=%.2f, instant=%.2f, atr_period=%d\n"
            "  Direction: long_only=%s, short_only=%s | max_trades=%d\n"
            "  Risk: sl_atr=%.1f, tp_atr=%.1f, kl_buffer=%.2f×ATR, trail_mode=%s\n"
            "  Entry types: breakout=%s, bounce=%s",
            "OR" if use_or else "Pure", timeframe, self._session_lookback,
            use_or, or_bars, or_start_time, entry_end_time, session_end_time,
            swing_period, cluster_tolerance, zone_tolerance, signal_level_count,
            breakout_buffer, bounce_buffer, instant_threshold, atr_period,
            long_only, short_only, max_trades_per_day,
            sl_atr_multiplier, tp_atr_multiplier, key_level_buffer, key_level_trail_mode,
            use_breakout, use_bounce,
        )

        # Daily state
        self._current_date: datetime | None = None
        self._current_trading_day = None  # date object for "business day"
        self._or_high: int | None = None
        self._or_low: int | None = None
        self._or_mid: int | None = None
        self._or_range: int | None = None
        self._or_calculated = False
        self._key_levels: list[KeyLevel] = []
        self._signal_levels: list[KeyLevel] = []
        self._trailing_levels: list[int] = []
        self._trades_today = 0
        self._atr: float = 0.0
        self._prev_close: int | None = None
        self._levels_calculated = False
        self._cooldown_until: datetime | None = None
        self._last_bar_time: datetime | None = None

    # ──────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────

    def evaluate(
        self,
        kbar_list: KBarList,
        current_price: float,
        symbol: str,
    ) -> StrategySignal:
        if len(kbar_list) < 2:
            return self._hold(symbol, current_price, "insufficient data")

        kbar = kbar_list.kbars[-1]
        prev_kbar = kbar_list.kbars[-2]
        bar_time = kbar.time
        self._last_bar_time = bar_time

        # Day change detection (trading-day aware for night sessions)
        trading_day = self._get_trading_day(bar_time)
        if self._current_trading_day is None or trading_day != self._current_trading_day:
            self._reset_daily_state()
            self._current_date = bar_time
            self._current_trading_day = trading_day
            _log("═══ New trading day: %s ═══", trading_day)

        # Only trade during active session
        if not self._is_active_session(bar_time):
            self._prev_close = int(kbar.close)
            return self._hold(symbol, current_price, "outside day session")

        # Calculate OR if using OR mode
        if self.use_or and not self._or_calculated:
            if not self._try_calculate_or(kbar_list):
                return self._hold(symbol, current_price, "waiting for OR")

        # Calculate key levels (once per day, after OR or at session start)
        if not self._levels_calculated:
            self._calculate_key_levels(kbar_list)
            if not self._signal_levels:
                return self._hold(symbol, current_price, "no key levels found")
            self._init_history_state(kbar_list)

        # ATR
        atr = self.indicator_service.calculate_atr(kbar_list, self.atr_period)
        if atr is None or atr <= 0:
            return self._hold(symbol, current_price, "ATR unavailable")
        self._atr = atr

        # Check trading window
        if not self._is_in_trading_window(bar_time):
            self._prev_close = int(kbar.close)
            return self._hold(symbol, current_price, "outside entry window")

        # Check trade limit
        if self._trades_today >= self.max_trades_per_day:
            self._prev_close = int(kbar.close)
            return self._hold(symbol, current_price, "max trades reached")

        # Cooldown: skip 1 bar after last exit
        if self._cooldown_until is not None:
            if bar_time <= self._cooldown_until:
                self._prev_close = int(kbar.close)
                return self._hold(symbol, current_price, "cooldown")
            self._cooldown_until = None

        # Detect signals
        signals = detect_signals(
            kbar,
            self._signal_levels,
            atr,
            prev_close=self._prev_close,
            breakout_buffer=self.breakout_buffer,
            bounce_buffer=self.bounce_buffer,
            instant_threshold=self.instant_threshold,
            current_price=current_price if self.is_live else None,
        )

        self._prev_close = int(kbar.close)

        if not signals:
            return self._hold(symbol, current_price, "no signal")

        _log(
            "[%s] %d signal(s) detected | bar: O=%d H=%d L=%d C=%d | ATR=%.1f | prev_close=%s",
            bar_time.strftime("%H:%M"), len(signals),
            int(kbar.open), int(kbar.high), int(kbar.low), int(kbar.close),
            atr, self._prev_close,
        )
        for i, s in enumerate(signals):
            _log(
                "  signal[%d]: %s @ level=%d (score=%.1f) | entry=%d | instant=%s",
                i, s.signal_type, s.key_level.price, s.score, s.entry_price, s.instant,
            )

        # Filter by direction
        for sig in signals:
            is_long = sig.signal_type in ("breakout_long", "bounce_long")
            is_short = sig.signal_type in ("breakout_short", "bounce_short")

            if is_long and self.short_only:
                _log("  SKIP %s: short_only mode", sig.signal_type)
                continue
            if is_short and self.long_only:
                _log("  SKIP %s: long_only mode", sig.signal_type)
                continue

            # Filter by entry type
            if "breakout" in sig.signal_type and not self.use_breakout:
                _log("  SKIP %s: breakout disabled", sig.signal_type)
                continue
            if "bounce" in sig.signal_type and not self.use_bounce:
                _log("  SKIP %s: bounce disabled", sig.signal_type)
                continue

            # OR filter: only long above OR_High, only short below OR_Low
            if self.use_or and self._or_calculated:
                close = int(kbar.close)
                if is_long and close < (self._or_high or 0):
                    _log(
                        "  SKIP %s: close=%d < OR_High=%d",
                        sig.signal_type, close, self._or_high,
                    )
                    continue
                if is_short and close > (self._or_low or 999999):
                    _log(
                        "  SKIP %s: close=%d > OR_Low=%d",
                        sig.signal_type, close, self._or_low,
                    )
                    continue

            # Valid signal found
            self._trades_today += 1
            entry_price = sig.entry_price
            signal_type = (
                SignalType.ENTRY_LONG if is_long else SignalType.ENTRY_SHORT
            )
            meta = self._build_entry_metadata(is_long, entry_price, sig, kbar)
            if sig.instant:
                meta["instant_entry"] = True
            reason = (
                f"{sig.signal_type} at {sig.key_level.price} "
                f"(score={sig.key_level.score:.1f})"
            )

            dir_str = "LONG" if is_long else "SHORT"
            _log(
                ">>> ENTRY %s | %s | level=%d (score=%.1f) | entry=%d | instant=%s | "
                "SL=%s | trail_levels=%s | trade#%d/%d",
                dir_str, sig.signal_type, sig.key_level.price, sig.score,
                entry_price, sig.instant,
                meta.get("override_stop_loss_price"),
                meta.get("key_levels"),
                self._trades_today, self.max_trades_per_day,
            )

            return StrategySignal(
                signal_type=signal_type,
                symbol=symbol,
                price=float(entry_price),
                confidence=min(sig.score / 20.0, 1.0),
                reason=reason,
                metadata=meta,
            )

        _log("  All signals filtered out")
        return self._hold(symbol, current_price, "signals filtered out")

    def get_pending_state(self) -> dict | None:
        if not self._levels_calculated:
            state: dict = {}
        else:
            state = {
                "signal_levels": [
                    {"price": kl.price, "score": kl.score}
                    for kl in self._signal_levels
                ],
            }
            if self.use_or and self._or_calculated:
                state["or_high"] = self._or_high
                state["or_low"] = self._or_low

        state["trades_today"] = self._trades_today
        if self._cooldown_until is not None:
            state["cooldown_until"] = self._cooldown_until.isoformat()

        return state if state else None

    def restore_state(self, state: dict) -> None:
        """Restore runtime state from persisted strategy_state."""
        if not state:
            return

        trades = state.get("trades_today")
        if trades is not None:
            self._trades_today = int(trades)
            _log("Restored trades_today=%d", self._trades_today)

        cd = state.get("cooldown_until")
        if cd is not None:
            try:
                self._cooldown_until = datetime.fromisoformat(cd)
                _log("Restored cooldown_until=%s", cd)
            except (ValueError, TypeError):
                pass

    def get_instant_trigger_prices(self) -> list[tuple[float, str]]:
        """Return (price, direction) tuples for instant breakout monitoring.

        direction is "above" or "below" — meaning the trigger fires
        when tick price goes above/below that price.
        """
        if not self._signal_levels or not self._levels_calculated:
            return []
        if self._atr <= 0:
            return []
        buf = self._atr * self.instant_threshold
        triggers: list[tuple[float, str]] = []
        for kl in self._signal_levels:
            triggers.append((kl.price + buf, "above"))
            triggers.append((kl.price - buf, "below"))
        return triggers

    def on_position_closed(self) -> None:
        tf_min = self._TF_MINUTES.get(self.timeframe, 5)
        ref = self._last_bar_time or datetime.now()
        mins_past = ref.minute % tf_min
        next_bar = ref.replace(second=0, microsecond=0) + timedelta(
            minutes=tf_min - mins_past,
        )
        self._cooldown_until = next_bar
        _log("Cooldown until %s (skip 1 bar)", next_bar.strftime("%H:%M:%S"))

    # ──────────────────────────────────────────────
    # Key level calculation
    # ──────────────────────────────────────────────

    def _calculate_key_levels(self, kbar_list: KBarList) -> None:
        """Build SessionData and run confluence detection.

        For larger timeframes (15m, 30m, 1h), aggregates multiple previous
        sessions so that the total number of kbars fed to swing/volume
        detectors is comparable to what 5m gets from a single session.
        OHLC and pivot points always use the latest session only.
        """
        if self._current_trading_day is None:
            return

        today = self._current_trading_day
        night_boundary = time(5, 0)
        day_end = time(13, 45)
        lookback = self._session_lookback

        day_ohlc: dict[str, int] = {}
        night_ohlc: dict[str, int] = {}

        day_sessions: dict = {}
        night_sessions: dict = {}

        for kbar in kbar_list.kbars:
            d = kbar.time.date()
            t = kbar.time.time()

            if self.or_start_time <= t < day_end and d < today:
                day_sessions.setdefault(d, []).append(kbar)
            elif t >= time(15, 0) and d < today:
                night_sessions.setdefault(d, []).append(kbar)
            elif t < night_boundary:
                ns_date = d - timedelta(days=1)
                if ns_date < today:
                    night_sessions.setdefault(ns_date, []).append(kbar)

        # OHLC from latest session (for pivot points)
        latest_day_kbars: list[KBar] = []
        if day_sessions:
            latest = max(day_sessions.keys())
            latest_day_kbars = sorted(day_sessions[latest], key=lambda k: k.time)
            day_ohlc = {
                "high": int(max(k.high for k in latest_day_kbars)),
                "low": int(min(k.low for k in latest_day_kbars)),
                "close": int(latest_day_kbars[-1].close),
            }

        latest_night_kbars: list[KBar] = []
        if night_sessions:
            latest = max(night_sessions.keys())
            latest_night_kbars = sorted(night_sessions[latest], key=lambda k: k.time)
            night_ohlc = {
                "high": int(max(k.high for k in latest_night_kbars)),
                "low": int(min(k.low for k in latest_night_kbars)),
                "close": int(latest_night_kbars[-1].close),
            }

        # Aggregate N most recent sessions for swing/volume detection
        if lookback <= 1:
            agg_day_kbars = latest_day_kbars
            agg_night_kbars = latest_night_kbars
        else:
            recent_day_dates = sorted(day_sessions.keys(), reverse=True)[:lookback]
            agg_day_kbars = []
            for dd in sorted(recent_day_dates):
                agg_day_kbars.extend(sorted(day_sessions[dd], key=lambda k: k.time))

            recent_night_dates = sorted(night_sessions.keys(), reverse=True)[:lookback]
            agg_night_kbars = []
            for nd in sorted(recent_night_dates):
                agg_night_kbars.extend(sorted(night_sessions[nd], key=lambda k: k.time))

        today_open = None
        for kbar in kbar_list.kbars:
            if (
                self._get_trading_day(kbar.time) == today
                and kbar.time.time() >= self.or_start_time
            ):
                today_open = int(kbar.open)
                break

        session = SessionData(
            prev_day_high=day_ohlc.get("high", 0),
            prev_day_low=day_ohlc.get("low", 0),
            prev_day_close=day_ohlc.get("close", 0),
            prev_night_high=night_ohlc.get("high"),
            prev_night_low=night_ohlc.get("low"),
            prev_night_close=night_ohlc.get("close"),
            today_open=today_open,
            or_range=self._or_range or 1,
            prev_day_kbars=agg_day_kbars,
            prev_night_kbars=agg_night_kbars,
        )

        _log(
            "=== Key Level Calculation [%s] ===\n"
            "  Session data: prev_day H/L/C=%s/%s/%s | prev_night H/L/C=%s/%s/%s\n"
            "  today_open=%s | or_range=%s | lookback=%d sessions\n"
            "  Kbar counts: day=%d, night=%d",
            self._current_date.strftime("%Y-%m-%d") if self._current_date else "?",
            day_ohlc.get("high"), day_ohlc.get("low"), day_ohlc.get("close"),
            night_ohlc.get("high"), night_ohlc.get("low"), night_ohlc.get("close"),
            today_open, self._or_range, lookback,
            len(agg_day_kbars), len(agg_night_kbars),
        )

        self._key_levels = find_confluence_levels(
            session,
            swing_period=self.swing_period,
            cluster_tolerance=self.cluster_tolerance,
            zone_tolerance=self.zone_tolerance,
            max_levels=20,
        )

        n = self.signal_level_count
        self._signal_levels = self._key_levels[:n]
        self._trailing_levels = sorted(
            [kl.price for kl in self._key_levels[n:]],
        )
        self._levels_calculated = True

        _log("  Total levels found: %d (signal=%d, trailing=%d)",
                 len(self._key_levels), len(self._signal_levels), len(self._trailing_levels))
        for i, kl in enumerate(self._key_levels):
            role = "SIGNAL" if i < n else "TRAIL"
            _log(
                "  [%s] #%d: price=%d | score=%.1f | touches=%d | sources=%s",
                role, i + 1, kl.price, kl.score, kl.touch_count,
                ", ".join(kl.sources),
            )
        if self._trailing_levels:
            _log("  Trailing ladder: %s", self._trailing_levels)

    def _init_history_state(self, kbar_list: KBarList) -> None:
        """Recover prev_close from today's kbar history.

        Called once after key levels are calculated, so that a mid-day restart
        has correct prev_close state for breakout detection (prev_close=None
        would bypass the "was price on the other side?" check).
        """
        if self._current_trading_day is None:
            return

        today = self._current_trading_day
        today_kbars = sorted(
            [
                k for k in kbar_list.kbars
                if self._get_trading_day(k.time) == today
                and k.time.time() >= self.or_start_time
            ],
            key=lambda k: k.time,
        )

        if self._prev_close is None and len(today_kbars) >= 2:
            self._prev_close = int(today_kbars[-2].close)
            _log("  Recovered prev_close from history: %d (from %d today bars)",
                 self._prev_close, len(today_kbars))

    # ──────────────────────────────────────────────
    # OR calculation
    # ──────────────────────────────────────────────

    def _try_calculate_or(self, kbar_list: KBarList) -> bool:
        if self._current_trading_day is None:
            return False

        today = self._current_trading_day
        day_end = time(13, 45)
        today_day_kbars = [
            k for k in kbar_list.kbars
            if self._get_trading_day(k.time) == today
            and self.or_start_time <= k.time.time() < day_end
        ]

        if len(today_day_kbars) < self.or_bars:
            return False

        or_kbars = today_day_kbars[:self.or_bars]
        self._or_high = int(max(k.high for k in or_kbars))
        self._or_low = int(min(k.low for k in or_kbars))
        self._or_mid = (self._or_high + self._or_low) // 2
        self._or_range = self._or_high - self._or_low
        self._or_calculated = True

        _log(
            "=== Opening Range [%s] ===\n"
            "  OR bars: %d | H=%d L=%d Mid=%d Range=%d\n"
            "  Filter: Long only above %d | Short only below %d",
            self._current_date.strftime("%Y-%m-%d") if self._current_date else "?",
            self.or_bars, self._or_high, self._or_low, self._or_mid, self._or_range,
            self._or_high, self._or_low,
        )
        return True

    # ──────────────────────────────────────────────
    # Entry metadata
    # ──────────────────────────────────────────────

    def _build_entry_metadata(
        self,
        is_long: bool,
        entry_price: int,
        sig,
        kbar: KBar,
    ) -> dict:
        atr = self._atr
        meta: dict = {
            "entry_type": sig.signal_type,
            "signal_level_price": sig.key_level.price,
            "signal_level_score": sig.key_level.score,
        }

        if self.use_or and self._or_calculated:
            meta["or_high"] = self._or_high
            meta["or_low"] = self._or_low
            meta["or_mid"] = self._or_mid
            meta["or_range"] = self._or_range

        # Stop loss — depends on signal type
        is_bounce = "bounce" in sig.signal_type
        if is_bounce:
            # Bounce: SL at the bar's extreme (the wick that touched the level)
            sl_price = int(kbar.low) if is_long else int(kbar.high)
        else:
            # Breakout: SL at the key level BELOW the signal level (not entry)
            signal_level = sig.key_level.price
            sl_price = self._find_sl_level(is_long, signal_level)
            if sl_price is not None:
                buf_pts = int(atr * self.key_level_buffer)
                sl_price = (
                    sl_price - buf_pts
                    if is_long
                    else sl_price + buf_pts
                )
        # Fallback: ATR-based
        if sl_price is None:
            sl_price = (
                entry_price - int(atr * self.sl_atr_multiplier)
                if is_long
                else entry_price + int(atr * self.sl_atr_multiplier)
            )
        meta["override_stop_loss_price"] = sl_price

        # Take profit: nearest key level on profit side, or ATR-based
        # tp_atr_multiplier=0 means no fixed TP (rely on trailing stop)
        if self.tp_atr_multiplier > 0:
            tp_price = self._find_tp_level(is_long, entry_price)
            if tp_price is None:
                tp_price = (
                    entry_price + int(atr * self.tp_atr_multiplier)
                    if is_long
                    else entry_price - int(atr * self.tp_atr_multiplier)
                )
            meta["override_take_profit_price"] = tp_price

        # Key levels for PM ladder trailing stop — use ALL key levels, not just trailing-only
        all_kl_prices = sorted(set(kl.price for kl in self._key_levels))
        if all_kl_prices:
            if is_long:
                levels = [p for p in all_kl_prices if p > entry_price]
            else:
                levels = sorted(
                    [p for p in all_kl_prices if p < entry_price],
                    reverse=True,
                )
            if levels:
                meta["key_levels"] = levels
                meta["key_level_buffer"] = int(atr * self.key_level_buffer)
                meta["key_level_trail_mode"] = self.key_level_trail_mode
                meta["key_level_atr"] = round(atr, 1)

        dir_str = "LONG" if is_long else "SHORT"
        _log(
            "  Entry metadata [%s]: entry=%d | SL=%s (method=%s) | TP=%s\n"
            "    signal_level=%d | trailing_levels=%s | trail_mode=%s",
            dir_str, entry_price,
            meta.get("override_stop_loss_price"), "bounce_extreme" if "bounce" in sig.signal_type else "next_kl",
            meta.get("override_take_profit_price", "none(TS only)"),
            sig.key_level.price,
            meta.get("key_levels", []),
            self.key_level_trail_mode,
        )

        return meta

    def _find_sl_level(self, is_long: bool, entry_price: int) -> int | None:
        """Find nearest key level on the loss side for SL."""
        all_prices = sorted(kl.price for kl in self._key_levels)
        if is_long:
            below = [p for p in all_prices if p < entry_price]
            return below[-1] if below else None
        else:
            above = [p for p in all_prices if p > entry_price]
            return above[0] if above else None

    def _find_tp_level(self, is_long: bool, entry_price: int) -> int | None:
        """Find nearest key level on the profit side for TP."""
        all_prices = sorted(kl.price for kl in self._key_levels)
        min_dist = int(self._atr * 0.5) if self._atr else 20
        if is_long:
            above = [p for p in all_prices if p > entry_price + min_dist]
            return above[0] if above else None
        else:
            below = [p for p in all_prices if p < entry_price - min_dist]
            return below[-1] if below else None

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    def _reset_daily_state(self) -> None:
        self._or_high = None
        self._or_low = None
        self._or_mid = None
        self._or_range = None
        self._or_calculated = False
        self._key_levels = []
        self._signal_levels = []
        self._trailing_levels = []
        self._trades_today = 0
        self._atr = 0.0
        self._levels_calculated = False
        self._cooldown_until = None

    def _get_trading_day(self, bar_time: datetime):
        """Return business date. Early-morning night-session bars belong to previous day."""
        t = bar_time.time()
        d = bar_time.date()
        if self._crosses_midnight and t < self.session_end_time:
            return d - timedelta(days=1)
        return d

    def _is_active_session(self, bar_time: datetime) -> bool:
        """Check if bar is within the active trading session (handles midnight crossover)."""
        t = bar_time.time()
        if self._crosses_midnight:
            return t >= self.or_start_time or t < self.session_end_time
        return self.or_start_time <= t < self.session_end_time

    def _is_in_trading_window(self, bar_time: datetime) -> bool:
        t = bar_time.time()
        if self.use_or and not self._or_calculated:
            return False
        start = self.or_start_time
        end = self.entry_end_time
        if end <= start:
            return t >= start or t <= end
        return start <= t and t <= end

    def _hold(self, symbol: str, price: float, reason: str) -> StrategySignal:
        return StrategySignal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            reason=reason,
        )

    def __repr__(self) -> str:
        parts = []
        if self.use_or:
            parts.append(f"OR(bars={self.or_bars})")
        else:
            parts.append("PureKL")
        parts.append(f"sig={self.signal_level_count}")
        parts.append(f"brk_buf={self.breakout_buffer}")
        parts.append(f"bnc_buf={self.bounce_buffer}")
        if self.long_only:
            parts.append("LongOnly")
        if self.short_only:
            parts.append("ShortOnly")
        parts.append(f"max{self.max_trades_per_day}x/day")
        if self.use_breakout and not self.use_bounce:
            parts.append("BreakoutOnly")
        elif self.use_bounce and not self.use_breakout:
            parts.append("BounceOnly")
        return f"KeyLevel({', '.join(parts)})"


def _parse_time(time_str: str) -> time:
    h, m = map(int, time_str.split(":"))
    return time(h, m)
