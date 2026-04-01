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
from auto_trade.services.key_level_signal import KeyLevelSignal
from auto_trade.strategies.base_strategy import BaseStrategy


import os as _os

_KL_VERBOSE = _os.environ.get("KL_VERBOSE", "1") == "1"


def _log(msg: str, *args, verbose: bool = False) -> None:
    """Print with [KL] prefix for easy grep."""
    if verbose and not _KL_VERBOSE:
        return
    text = msg % args if args else msg
    print(f"[KL] {text}")


class KeyLevelStrategy(BaseStrategy):

    _TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
    _EXCHANGE_DAY_START = time(8, 45)
    _EXCHANGE_DAY_END = time(13, 45)
    _EXCHANGE_NIGHT_START = time(15, 0)
    _EXCHANGE_NIGHT_END = time(5, 0)

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
        max_trades_day_session: int | None = None,
        max_trades_night_session: int | None = None,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 2.0,
        key_level_buffer: float = 0.15,
        key_level_trail_mode: str = "current",  # "current" or "previous"
        # --- Entry types ---
        use_breakout: bool = True,
        use_bounce: bool = True,
        # --- Trend filter ---
        trend_filter: str = "or",  # "or", "ema", "none"
        trend_filter_ema_period: int = 200,
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
        self.max_trades_day_session = max_trades_day_session
        self.max_trades_night_session = max_trades_night_session
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier
        self.key_level_buffer = key_level_buffer
        self.key_level_trail_mode = key_level_trail_mode

        # Entry types
        self.use_breakout = use_breakout
        self.use_bounce = use_bounce

        # Per-direction overrides (fallback to base value when not set)
        self.use_breakout_long = kwargs.get("use_breakout_long", use_breakout)
        self.use_breakout_short = kwargs.get("use_breakout_short", use_breakout)
        self.use_bounce_long = kwargs.get("use_bounce_long", use_bounce)
        self.use_bounce_short = kwargs.get("use_bounce_short", use_bounce)
        self.use_instant_long = kwargs.get("use_instant_long", True)
        self.use_instant_short = kwargs.get("use_instant_short", True)
        self.breakout_buffer_long = kwargs.get("breakout_buffer_long", breakout_buffer)
        self.breakout_buffer_short = kwargs.get("breakout_buffer_short", breakout_buffer)
        self.instant_threshold_long = kwargs.get("instant_threshold_long", instant_threshold)
        self.instant_threshold_short = kwargs.get("instant_threshold_short", instant_threshold)
        self.bounce_buffer_long = kwargs.get("bounce_buffer_long", bounce_buffer)
        self.bounce_buffer_short = kwargs.get("bounce_buffer_short", bounce_buffer)
        self.bounce_ignore_or = kwargs.get("bounce_ignore_or", False)

        # Trend filter
        self.trend_filter = trend_filter
        self.trend_filter_ema_period = trend_filter_ema_period

        _log(
            "=== KeyLevelStrategy initialized ===\n"
            "  mode=%s | timeframe=%s | session_lookback=%d\n"
            "  OR: use=%s, bars=%d, start=%s, entry_end=%s, session_end=%s\n"
            "  KL detect: swing=%d, cluster_tol=%d, zone_tol=%d, signal_count=%d\n"
            "  Signal: brk_buf=%.2f, bnc_buf=%.2f, instant=%.2f, atr_period=%d\n"
            "  Direction: long_only=%s, short_only=%s | max_trades=%d"
            " | max_day=%s | max_night=%s\n"
            "  Risk: sl_atr=%.1f, tp_atr=%.1f, kl_buffer=%.2f×ATR, trail_mode=%s\n"
            "  Entry types: breakout=%s, bounce=%s",
            "OR" if use_or else "Pure", timeframe, self._session_lookback,
            use_or, or_bars, or_start_time, entry_end_time, session_end_time,
            swing_period, cluster_tolerance, zone_tolerance, signal_level_count,
            breakout_buffer, bounce_buffer, instant_threshold, atr_period,
            long_only, short_only, max_trades_per_day,
            max_trades_day_session, max_trades_night_session,
            sl_atr_multiplier, tp_atr_multiplier, key_level_buffer, key_level_trail_mode,
            use_breakout, use_bounce,
        )

        # Log per-direction overrides (only when different from base)
        overrides = []
        if self.use_breakout_long != use_breakout:
            overrides.append(f"breakout_long={self.use_breakout_long}")
        if self.use_breakout_short != use_breakout:
            overrides.append(f"breakout_short={self.use_breakout_short}")
        if self.use_bounce_long != use_bounce:
            overrides.append(f"bounce_long={self.use_bounce_long}")
        if self.use_bounce_short != use_bounce:
            overrides.append(f"bounce_short={self.use_bounce_short}")
        if not self.use_instant_long:
            overrides.append("instant_long=False")
        if not self.use_instant_short:
            overrides.append("instant_short=False")
        if self.breakout_buffer_long != breakout_buffer:
            overrides.append(f"bb_long={self.breakout_buffer_long:.2f}")
        if self.breakout_buffer_short != breakout_buffer:
            overrides.append(f"bb_short={self.breakout_buffer_short:.2f}")
        if self.instant_threshold_long != instant_threshold:
            overrides.append(f"ib_long={self.instant_threshold_long:.2f}")
        if self.instant_threshold_short != instant_threshold:
            overrides.append(f"ib_short={self.instant_threshold_short:.2f}")
        if self.bounce_buffer_long != bounce_buffer:
            overrides.append(f"bnc_long={self.bounce_buffer_long:.2f}")
        if self.bounce_buffer_short != bounce_buffer:
            overrides.append(f"bnc_short={self.bounce_buffer_short:.2f}")
        if self.bounce_ignore_or:
            overrides.append("bounce_ignore_or=True")
        if overrides:
            _log("  Per-direction: %s", ", ".join(overrides))

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
        self._trades_day_session = 0
        self._trades_night_session = 0
        self._atr: float = 0.0
        self._levels_calculated = False
        self._cooldown_until: datetime | None = None
        self._last_bar_time: datetime | None = None

        # Targeted breakout tracking
        self._target_long: KeyLevel | None = None
        self._target_short: KeyLevel | None = None
        self._instant_target_long: KeyLevel | None = None
        self._instant_target_short: KeyLevel | None = None

    # ──────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────

    def evaluate(
        self,
        kbar_list: KBarList,
        current_price: float,
        symbol: str,
        bar_close: bool = True,
    ) -> StrategySignal:
        """Evaluate strategy and return a signal.

        Args:
            bar_close: True when called at a bar boundary (regular check).
                       False when called mid-bar from an instant trigger.
                       Controls which buffer is used and whether state is updated.
        """
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
            return self._hold(symbol, current_price, "outside day session")

        # Calculate OR if using OR-based trend filter
        if self.trend_filter == "or" and self.use_or and not self._or_calculated:
            if not self._try_calculate_or(kbar_list):
                return self._hold(symbol, current_price, "waiting for OR")

        if not self._levels_calculated:
            self._calculate_key_levels(kbar_list)
            if not self._signal_levels:
                return self._hold(symbol, current_price, "no key levels found")

        # ATR
        atr = self.indicator_service.calculate_atr(kbar_list, self.atr_period)
        if atr is None or atr <= 0:
            return self._hold(symbol, current_price, "ATR unavailable")
        self._atr = atr

        # Compute targets from kbar data (always fresh, no stale state)
        self._compute_active_targets(kbar_list)

        # Check trading window
        if not self._is_in_trading_window(bar_time):
            return self._hold(symbol, current_price, "outside entry window")

        # Check trade limit
        session_name = self._get_trade_session(bar_time)
        if self._reached_trade_limit(session_name):
            return self._hold(symbol, current_price, "max trades reached")

        # Cooldown: skip 1 bar after last exit
        if self._cooldown_until is not None:
            if bar_time <= self._cooldown_until:
                return self._hold(symbol, current_price, "cooldown")
            self._cooldown_until = None

        # === Direction gate (OR / EMA / config) ===
        allow_long, allow_short = self._get_allowed_directions(
            current_price, kbar_list, kbar,
        )
        if not allow_long and not allow_short:
            return self._hold(symbol, current_price, "no direction allowed")

        # === Breakout check (targeted: only the next level in each direction) ===
        signal = self._check_breakout_target(
            current_price, kbar, prev_kbar,
            allow_long, allow_short, bar_close, symbol,
        )
        if signal:
            return signal

        # === Bounce check (bar close only, scans all signal levels) ===
        if bar_close:
            if self.bounce_ignore_or:
                bounce_long = not self.short_only
                bounce_short = not self.long_only
            else:
                bounce_long, bounce_short = allow_long, allow_short
            signal = self._check_bounce(
                kbar, prev_kbar, bounce_long, bounce_short, symbol,
            )
            if signal:
                return signal

        return self._hold(symbol, current_price, "no signal")

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
            if self._target_long:
                state["target_long"] = self._target_long.price
            if self._target_short:
                state["target_short"] = self._target_short.price
            if self._instant_target_long:
                state["instant_target_long"] = self._instant_target_long.price
            if self._instant_target_short:
                state["instant_target_short"] = self._instant_target_short.price

        state["trades_today"] = self._trades_today
        state["trades_day_session"] = self._trades_day_session
        state["trades_night_session"] = self._trades_night_session
        if self._cooldown_until is not None:
            state["cooldown_until"] = self._cooldown_until.isoformat()
        if self._current_trading_day is not None:
            state["trading_day"] = str(self._current_trading_day)

        return state if state else None

    def restore_state(self, state: dict) -> None:
        """Restore runtime state from persisted strategy_state."""
        if not state:
            return

        from datetime import date as _date

        td = state.get("trading_day")
        if td is not None:
            try:
                self._current_trading_day = _date.fromisoformat(td)
                _log("Restored trading_day=%s", td)
            except (ValueError, TypeError):
                pass

        trades = state.get("trades_today")
        if trades is not None:
            self._trades_today = int(trades)
            _log("Restored trades_today=%d", self._trades_today)

        day_trades = state.get("trades_day_session")
        if day_trades is not None:
            self._trades_day_session = int(day_trades)
            _log("Restored trades_day_session=%d", self._trades_day_session)

        night_trades = state.get("trades_night_session")
        if night_trades is not None:
            self._trades_night_session = int(night_trades)
            _log("Restored trades_night_session=%d", self._trades_night_session)

        cd = state.get("cooldown_until")
        if cd is not None:
            try:
                self._cooldown_until = datetime.fromisoformat(cd)
                _log("Restored cooldown_until=%s", cd)
            except (ValueError, TypeError):
                pass

    def get_instant_targets(self) -> tuple[float | None, float | None]:
        """Return (long_trigger_price, short_trigger_price) for instant monitoring.

        Already filtered by OR direction, cooldown, and trade limits.
        The engine only needs to compare tick price against these two values.
        """
        if not self._levels_calculated or self._atr <= 0:
            return None, None
        if self._cooldown_until and datetime.now() < self._cooldown_until:
            return None, None

        long_price = None
        short_price = None

        # Long target
        if (self.use_breakout_long and self.use_instant_long
                and self._instant_target_long and not self.short_only):
            buf_l = self._atr * self.instant_threshold_long
            tp = self._instant_target_long.price + buf_l
            if self.trend_filter == "or" and self.use_or and self._or_calculated:
                if self._or_high is not None and tp < self._or_high:
                    pass
                else:
                    long_price = tp
            else:
                long_price = tp

        # Short target
        if (self.use_breakout_short and self.use_instant_short
                and self._instant_target_short and not self.long_only):
            buf_s = self._atr * self.instant_threshold_short
            tp = self._instant_target_short.price - buf_s
            if self.trend_filter == "or" and self.use_or and self._or_calculated:
                if self._or_low is not None and tp > self._or_low:
                    pass
                else:
                    short_price = tp
            else:
                short_price = tp

        return long_price, short_price

    def get_instant_trigger_prices(self) -> list[tuple[float, str]]:
        """Legacy wrapper — adapts get_instant_targets to old list format."""
        long_p, short_p = self.get_instant_targets()
        triggers: list[tuple[float, str]] = []
        if long_p is not None:
            triggers.append((long_p, "above"))
        if short_p is not None:
            triggers.append((short_p, "below"))
        return triggers

    def on_position_closed(self, exit_price: int | None = None) -> None:
        tf_min = self._TF_MINUTES.get(self.timeframe, 5)
        now = datetime.now()
        mins_past = now.minute % tf_min
        next_bar = now.replace(second=0, microsecond=0) + timedelta(
            minutes=tf_min - mins_past,
        )
        self._cooldown_until = next_bar
        _log("Cooldown until %s (skip 1 bar)", next_bar.strftime("%H:%M:%S"))

    # ──────────────────────────────────────────────
    # Key level calculation
    # ──────────────────────────────────────────────

    _MAX_LOOKBACK = 5
    _BASE_RECENCY_POOL = 20

    def _calculate_key_levels(self, kbar_list: KBarList) -> None:
        """Build SessionData and run confluence detection.

        Auto-expands lookback (up to _MAX_LOOKBACK sessions) when no
        signal KL exists above or below the OR mid.  Expansion adds
        above/below-anchor KLs from extended data into the base
        recency pool, then re-selects top 15 by score.
        OHLC/pivots use latest session only.
        """
        if self._current_trading_day is None:
            return

        today = self._current_trading_day

        day_ohlc: dict[str, int] = {}
        night_ohlc: dict[str, int] = {}

        day_sessions: dict = {}
        night_sessions: dict = {}

        in_night_session = (
            self._current_date is not None
            and self._current_date.time() >= self._EXCHANGE_NIGHT_START
        )

        for kbar in kbar_list.kbars:
            d = kbar.time.date()
            t = kbar.time.time()

            if self._EXCHANGE_DAY_START <= t < self._EXCHANGE_DAY_END:
                if d < today or (d == today and in_night_session):
                    day_sessions.setdefault(d, []).append(kbar)
            elif t >= self._EXCHANGE_NIGHT_START and d < today:
                night_sessions.setdefault(d, []).append(kbar)
            elif t < self._EXCHANGE_NIGHT_END:
                ns_date = d - timedelta(days=1)
                if ns_date < today:
                    night_sessions.setdefault(ns_date, []).append(kbar)

        # OHLC from latest session (for pivot points) — always single session
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

        today_open = None
        for kbar in kbar_list.kbars:
            if (
                self._get_trading_day(kbar.time) == today
                and self._is_active_session(kbar.time)
            ):
                today_open = int(kbar.open)
                break

        or_mid = None
        if self._or_high is not None and self._or_low is not None:
            or_mid = (self._or_high + self._or_low) // 2

        anchor = or_mid or today_open or day_ohlc.get("close", 0)

        def _build_session_data(lb):
            if lb <= 1:
                d_kbars, n_kbars = latest_day_kbars, latest_night_kbars
            else:
                d_dates = sorted(day_sessions.keys(), reverse=True)[:lb]
                d_kbars = []
                for dd in sorted(d_dates):
                    d_kbars.extend(sorted(day_sessions[dd], key=lambda k: k.time))
                n_dates = sorted(night_sessions.keys(), reverse=True)[:lb]
                n_kbars = []
                for nd in sorted(n_dates):
                    n_kbars.extend(sorted(night_sessions[nd], key=lambda k: k.time))
            return SessionData(
                prev_day_high=day_ohlc.get("high", 0),
                prev_day_low=day_ohlc.get("low", 0),
                prev_day_close=day_ohlc.get("close", 0),
                prev_night_high=night_ohlc.get("high"),
                prev_night_low=night_ohlc.get("low"),
                prev_night_close=night_ohlc.get("close"),
                today_open=today_open,
                or_range=self._or_range or 1,
                prev_day_kbars=d_kbars,
                prev_night_kbars=n_kbars,
            ), d_kbars, n_kbars

        # Step 1: base run — recency pool 20 → get pool (max_levels=20)
        base_lookback = self._session_lookback
        base_session, agg_day_kbars, agg_night_kbars = _build_session_data(base_lookback)
        base_pool = find_confluence_levels(
            base_session,
            swing_period=self.swing_period,
            cluster_tolerance=self.cluster_tolerance,
            zone_tolerance=self.zone_tolerance,
            max_levels=self._BASE_RECENCY_POOL,
            recency_pool=self._BASE_RECENCY_POOL,
        )

        # Score sort → top 15
        base_pool.sort(key=lambda z: z.score, reverse=True)
        levels = base_pool[:15]
        lookback = base_lookback

        # Step 2: check signal coverage, expand if needed
        n_signal = self.signal_level_count
        max_lb = min(self._MAX_LOOKBACK, max(len(day_sessions), len(night_sessions), 1))
        zt = self.zone_tolerance

        if anchor and max_lb > base_lookback:
            signal_kls = levels[:n_signal]
            sig_above = sum(1 for kl in signal_kls if kl.price >= anchor)
            sig_below = sum(1 for kl in signal_kls if kl.price < anchor)
            missing_above = sig_above < 1
            missing_below = sig_below < 1

            if missing_above or missing_below:
                existing_prices = {kl.price for kl in base_pool}

                for lb in range(base_lookback + 1, max_lb + 1):
                    exp_session, agg_day_kbars, agg_night_kbars = _build_session_data(lb)
                    all_zones = find_confluence_levels(
                        exp_session,
                        swing_period=self.swing_period,
                        cluster_tolerance=self.cluster_tolerance,
                        zone_tolerance=self.zone_tolerance,
                        max_levels=999,
                        recency_pool=999,
                    )

                    # Add missing-direction KLs to pool (de-dup by price proximity)
                    for z in all_zones:
                        if any(abs(z.price - ep) <= zt for ep in existing_prices):
                            continue
                        if missing_above and z.price >= anchor:
                            base_pool.append(z)
                            existing_prices.add(z.price)
                        elif missing_below and z.price < anchor:
                            base_pool.append(z)
                            existing_prices.add(z.price)

                    base_pool.sort(key=lambda z: z.score, reverse=True)
                    levels = base_pool[:15]
                    lookback = lb

                    signal_kls = levels[:n_signal]
                    sig_above = sum(1 for kl in signal_kls if kl.price >= anchor)
                    sig_below = sum(1 for kl in signal_kls if kl.price < anchor)

                    if (not missing_above or sig_above >= 1) and (not missing_below or sig_below >= 1):
                        _log("  Auto-expanded lookback: %d → %d (sig_above=%d, sig_below=%d, pool=%d)",
                             base_lookback, lb, sig_above, sig_below, len(base_pool))
                        break

                if lookback > base_lookback and lookback >= max_lb:
                    _log("  Auto-expanded lookback: %d → %d (max, sig_above=%d, sig_below=%d, pool=%d)",
                         base_lookback, lookback, sig_above, sig_below, len(base_pool))

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

        self._key_levels = levels

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

    # ──────────────────────────────────────────────
    # OR calculation
    # ──────────────────────────────────────────────

    def _try_calculate_or(self, kbar_list: KBarList) -> bool:
        if self._current_trading_day is None:
            return False

        today = self._current_trading_day
        today_session_kbars = [
            k for k in kbar_list.kbars
            if self._get_trading_day(k.time) == today
            and self._is_active_session(k.time)
        ]

        if len(today_session_kbars) < self.or_bars:
            return False

        or_kbars = today_session_kbars[:self.or_bars]
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
        self._trades_day_session = 0
        self._trades_night_session = 0
        self._atr = 0.0
        self._levels_calculated = False
        self._cooldown_until = None
        self._target_long = None
        self._target_short = None
        self._instant_target_long = None
        self._instant_target_short = None

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

    def _get_allowed_directions(
        self, price: float, kbar_list: KBarList, kbar,
    ) -> tuple[bool, bool]:
        """Pre-gate: determine (allow_long, allow_short) from config + trend filter."""
        allow_long = not self.short_only
        allow_short = not self.long_only

        if self.trend_filter == "or" and self.use_or and self._or_calculated:
            p = int(price)
            if p < (self._or_high or 0):
                allow_long = False
            if p > (self._or_low or 999999):
                allow_short = False
        elif self.trend_filter == "ema":
            ema_list = self.indicator_service.calculate_ema(
                kbar_list, self.trend_filter_ema_period,
            )
            if ema_list and len(ema_list) > 0:
                ema_val = ema_list[-1].ema_value
                close = float(kbar.close)
                if close < ema_val:
                    allow_long = False
                if close > ema_val:
                    allow_short = False

        return allow_long, allow_short

    def _compute_active_targets(self, kbar_list: KBarList) -> None:
        """Determine the next breakout target KL in each direction.

        Computes two sets of targets:
          - bar-close targets (ref = prev_kbar.close): for regular bar evaluations
          - instant targets (ref = kbar.open): for mid-bar instant monitoring
        """
        self._target_long = None
        self._target_short = None
        self._instant_target_long = None
        self._instant_target_short = None

        if not self._signal_levels or self._atr <= 0 or len(kbar_list) < 2:
            return

        kbar = kbar_list.kbars[-1]
        prev_kbar = kbar_list.kbars[-2]
        bar_ref = int(prev_kbar.close)
        instant_ref = int(kbar.open)

        levels_asc = sorted(self._signal_levels, key=lambda kl: kl.price)
        buf = self._atr * self.breakout_buffer

        if self.use_breakout and not self.short_only:
            for kl in levels_asc:
                if self._target_long is None and bar_ref <= kl.price + buf:
                    self._target_long = kl
                if self._instant_target_long is None and instant_ref <= kl.price + buf:
                    self._instant_target_long = kl
                if self._target_long and self._instant_target_long:
                    break

        if self.use_breakout and not self.long_only:
            for kl in reversed(levels_asc):
                if self._target_short is None and bar_ref >= kl.price - buf:
                    self._target_short = kl
                if self._instant_target_short is None and instant_ref >= kl.price - buf:
                    self._instant_target_short = kl
                if self._target_short and self._instant_target_short:
                    break

        _log(
            "  Targets: long=%s short=%s | instant_long=%s instant_short=%s | bar_ref=%d instant_ref=%d",
            self._target_long.price if self._target_long else None,
            self._target_short.price if self._target_short else None,
            self._instant_target_long.price if self._instant_target_long else None,
            self._instant_target_short.price if self._instant_target_short else None,
            bar_ref, instant_ref,
            verbose=True,
        )

    def _check_breakout_target(
        self,
        current_price: float,
        kbar,
        prev_kbar,
        allow_long: bool,
        allow_short: bool,
        bar_close: bool,
        symbol: str,
    ) -> StrategySignal | None:
        """Check if price has broken through the active target level.

        Reference price for "was on the other side":
          - bar_close: prev_kbar.close (the completed bar before current)
          - instant: kbar.open (current bar's open)
        """
        if self._atr <= 0:
            return None

        session_name = self._get_trade_session(kbar.time)

        # --- Long breakout ---
        long_kl = self._target_long if bar_close else self._instant_target_long
        if allow_long and self.use_breakout_long and long_kl:
            bbuf = self._atr * self.breakout_buffer_long
            ibuf = self._atr * self.instant_threshold_long
            kl = long_kl
            ref = int(prev_kbar.close) if bar_close else int(kbar.open)

            if bar_close and not self.is_live:
                if (int(kbar.high) > kl.price + ibuf
                        and int(kbar.low) <= kl.price + bbuf
                        and ref <= kl.price + bbuf):
                    return self._emit_entry(
                        True, kl, int(kl.price + ibuf), True,
                        kbar, session_name, "breakout_long", symbol,
                    )
                if (int(kbar.close) > kl.price + bbuf
                        and ref <= kl.price + bbuf):
                    return self._emit_entry(
                        True, kl, int(kbar.close), False,
                        kbar, session_name, "breakout_long", symbol,
                    )

            elif bar_close and self.is_live:
                if (int(kbar.close) > kl.price + bbuf
                        and ref <= kl.price + bbuf):
                    return self._emit_entry(
                        True, kl, int(kbar.close), False,
                        kbar, session_name, "breakout_long", symbol,
                    )

            else:
                if (self.use_instant_long
                        and int(current_price) > kl.price + ibuf
                        and ref <= kl.price + bbuf):
                    return self._emit_entry(
                        True, kl, int(current_price), True,
                        kbar, session_name, "breakout_long", symbol,
                    )

        # --- Short breakout ---
        short_kl = self._target_short if bar_close else self._instant_target_short
        if allow_short and self.use_breakout_short and short_kl:
            bbuf = self._atr * self.breakout_buffer_short
            ibuf = self._atr * self.instant_threshold_short
            kl = short_kl
            ref = int(prev_kbar.close) if bar_close else int(kbar.open)

            if bar_close and not self.is_live:
                if (int(kbar.low) < kl.price - ibuf
                        and int(kbar.high) >= kl.price - bbuf
                        and ref >= kl.price - bbuf):
                    return self._emit_entry(
                        False, kl, int(kl.price - ibuf), True,
                        kbar, session_name, "breakout_short", symbol,
                    )
                if (int(kbar.close) < kl.price - bbuf
                        and ref >= kl.price - bbuf):
                    return self._emit_entry(
                        False, kl, int(kbar.close), False,
                        kbar, session_name, "breakout_short", symbol,
                    )

            elif bar_close and self.is_live:
                if (int(kbar.close) < kl.price - bbuf
                        and ref >= kl.price - bbuf):
                    return self._emit_entry(
                        False, kl, int(kbar.close), False,
                        kbar, session_name, "breakout_short", symbol,
                    )

            else:
                if (self.use_instant_short
                        and int(current_price) < kl.price - ibuf
                        and ref >= kl.price - bbuf):
                    return self._emit_entry(
                        False, kl, int(current_price), True,
                        kbar, session_name, "breakout_short", symbol,
                    )

        return None

    def _check_bounce(
        self,
        kbar,
        prev_kbar,
        allow_long: bool,
        allow_short: bool,
        symbol: str,
    ) -> StrategySignal | None:
        """Check bounce signals at bar close (scan all signal levels)."""
        if (not self.use_bounce_long and not self.use_bounce_short) or self._atr <= 0:
            return None

        buf_long = self._atr * self.bounce_buffer_long
        buf_short = self._atr * self.bounce_buffer_short
        close = int(kbar.close)
        high = int(kbar.high)
        low = int(kbar.low)
        ref = int(prev_kbar.close)
        session_name = self._get_trade_session(kbar.time)

        for kl in self._signal_levels:
            level = kl.price

            if allow_long and self.use_bounce_long and low <= level + buf_long and close > level:
                if ref > level:
                    return self._emit_entry(
                        True, kl, close, False,
                        kbar, session_name, "bounce_long", symbol,
                    )

            if allow_short and self.use_bounce_short and high >= level - buf_short and close < level:
                if ref < level:
                    return self._emit_entry(
                        False, kl, close, False,
                        kbar, session_name, "bounce_short", symbol,
                    )

        return None

    def _emit_entry(
        self,
        is_long: bool,
        kl: KeyLevel,
        entry_price: int,
        instant: bool,
        kbar,
        session_name: str | None,
        signal_type_str: str,
        symbol: str,
    ) -> StrategySignal:
        """Build and return an entry signal, updating trade counters."""
        self._trades_today += 1
        if session_name == "day":
            self._trades_day_session += 1
        elif session_name == "night":
            self._trades_night_session += 1

        sig = KeyLevelSignal(
            signal_type=signal_type_str,
            key_level=kl,
            entry_price=entry_price,
            instant=instant,
            score=kl.score,
        )
        meta = self._build_entry_metadata(is_long, entry_price, sig, kbar)
        if instant:
            meta["instant_entry"] = True

        reason = f"{signal_type_str} at {kl.price} (score={kl.score:.1f})"
        dir_str = "LONG" if is_long else "SHORT"
        _log(
            ">>> ENTRY %s | %s | level=%d (score=%.1f) | entry=%d | instant=%s | "
            "SL=%s | trail_levels=%s | trade#%d/%d | session=%s d=%d n=%d",
            dir_str, signal_type_str, kl.price, kl.score,
            entry_price, instant,
            meta.get("override_stop_loss_price"),
            meta.get("key_levels"),
            self._trades_today, self.max_trades_per_day,
            session_name,
            self._trades_day_session,
            self._trades_night_session,
        )

        return StrategySignal(
            signal_type=SignalType.ENTRY_LONG if is_long else SignalType.ENTRY_SHORT,
            symbol=symbol,
            price=float(entry_price),
            confidence=min(kl.score / 20.0, 1.0),
            reason=reason,
            metadata=meta,
        )

    def _pass_trend_filter(
        self,
        kbar_list: KBarList,
        kbar,
        is_long: bool,
        is_short: bool,
        sig,
        current_price: float | None = None,
    ) -> bool:
        """Apply the configured trend filter. Returns True if signal passes."""
        if self.trend_filter == "none":
            return True

        if self.trend_filter == "or":
            if self.use_or and self._or_calculated:
                price = int(current_price) if current_price is not None else int(kbar.close)
                if is_long and price < (self._or_high or 0):
                    _log(
                        "  SKIP %s: price=%d < OR_High=%d",
                        sig.signal_type, price, self._or_high,
                    )
                    return False
                if is_short and price > (self._or_low or 999999):
                    _log(
                        "  SKIP %s: price=%d > OR_Low=%d",
                        sig.signal_type, price, self._or_low,
                    )
                    return False
            return True

        if self.trend_filter == "ema":
            ema_list = self.indicator_service.calculate_ema(
                kbar_list, self.trend_filter_ema_period,
            )
            if not ema_list or len(ema_list) == 0:
                return True
            ema_val = ema_list[-1].ema_value
            close = float(kbar.close)
            if is_long and close < ema_val:
                _log(
                    "  SKIP %s: close=%.0f < EMA%d=%.0f",
                    sig.signal_type, close,
                    self.trend_filter_ema_period, ema_val,
                )
                return False
            if is_short and close > ema_val:
                _log(
                    "  SKIP %s: close=%.0f > EMA%d=%.0f",
                    sig.signal_type, close,
                    self.trend_filter_ema_period, ema_val,
                )
                return False
            return True

        return True

    def _is_in_trading_window(self, bar_time: datetime) -> bool:
        t = bar_time.time()
        if self.trend_filter == "or" and self.use_or and not self._or_calculated:
            return False
        start = self.or_start_time
        end = self.entry_end_time
        if end <= start:
            return t >= start or t <= end
        return start <= t and t <= end

    def _get_trade_session(self, bar_time: datetime) -> str | None:
        """Return the current trading session bucket for separate trade caps."""
        t = bar_time.time()

        if self._EXCHANGE_DAY_START <= t < self._EXCHANGE_DAY_END:
            return "day"
        if t >= self._EXCHANGE_NIGHT_START or t < self._EXCHANGE_NIGHT_END:
            return "night"
        return None

    def _reached_trade_limit(self, session_name: str | None) -> bool:
        """Check either per-session or total daily trade limits."""
        if (
            self.max_trades_day_session is not None
            or self.max_trades_night_session is not None
        ):
            if session_name == "day" and self.max_trades_day_session is not None:
                return self._trades_day_session >= self.max_trades_day_session
            if session_name == "night" and self.max_trades_night_session is not None:
                return self._trades_night_session >= self.max_trades_night_session
            return False

        return self._trades_today >= self.max_trades_per_day

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
