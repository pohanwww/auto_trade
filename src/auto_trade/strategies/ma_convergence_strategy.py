"""MA Convergence Breakout Strategy (均線糾纏後突破).

當多條 EMA (5, 10, 20, 60) 在一段時間內收斂（糾纏），
然後價格突破所有均線方向時進場。30m 時間尺度。
"""

from datetime import datetime

from auto_trade.models.market import KBarList
from auto_trade.models.strategy import SignalType, StrategySignal
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.strategies.base_strategy import BaseStrategy


class MAConvergenceStrategy(BaseStrategy):
    """均線糾纏突破策略

    進場條件：
    1. 4 條 EMA 在近期處於糾纏狀態（spread < 價格 * threshold_pct%）
    2. 糾纏持續至少 convergence_min_bars 根 K 棒
    3. 價格收盤突破所有 EMA（做多：收在所有 EMA 之上，做空反之）
    4. EMA spread 正在擴張（均線開花）

    此策略只負責產生進場信號，停損/停利由 PositionManager 管理。
    """

    def __init__(
        self,
        indicator_service: IndicatorService,
        ema_periods: list[int] | None = None,
        convergence_threshold_pct: float = 0.3,
        convergence_min_bars: int = 3,
        long_only: bool = True,
        volume_confirm: bool = False,
        volume_percentile_threshold: float = 0.5,
        volume_percentile_lookback: int = 100,
        cooldown_bars: int = 5,
        **kwargs,  # noqa: ARG002
    ):
        super().__init__(indicator_service, name="MA Convergence Breakout")
        self.ema_periods = ema_periods or [5, 10, 20, 60]
        self.convergence_threshold_pct = convergence_threshold_pct
        self.convergence_min_bars = convergence_min_bars
        self.long_only = long_only
        self.volume_confirm = volume_confirm
        self.volume_percentile_threshold = volume_percentile_threshold
        self.volume_percentile_lookback = volume_percentile_lookback
        self.cooldown_bars = cooldown_bars

        self._bars_since_entry = 0
        self._had_position = False

    def on_position_closed(self) -> None:
        self._bars_since_entry = 0
        self._had_position = False

    def evaluate(
        self,
        kbar_list: KBarList,
        current_price: float,
        symbol: str,
    ) -> StrategySignal:
        now = datetime.now()

        if len(kbar_list) < max(self.ema_periods) + self.convergence_min_bars + 10:
            return StrategySignal(
                signal_type=SignalType.HOLD,
                symbol=symbol,
                price=current_price,
                reason="Insufficient data for MA convergence",
                timestamp=now,
            )

        # Cooldown after recent entry
        if self._had_position:
            self._bars_since_entry += 1
            if self._bars_since_entry < self.cooldown_bars:
                return StrategySignal(
                    signal_type=SignalType.HOLD,
                    symbol=symbol,
                    price=current_price,
                    reason=f"Cooldown: {self._bars_since_entry}/{self.cooldown_bars}",
                    timestamp=now,
                )
            self._had_position = False

        conv = self.indicator_service.detect_ma_convergence(
            kbar_list,
            periods=self.ema_periods,
            threshold_pct=self.convergence_threshold_pct,
            min_bars=self.convergence_min_bars,
        )

        ema_vals = conv["ema_values"]
        spread_pct = conv["spread_pct"]
        was_converged = conv["was_converged"]
        breakout_long = conv["breakout_long"]
        breakout_short = conv["breakout_short"]
        spread_expanding = conv["spread_expanding"]

        if not ema_vals:
            return StrategySignal(
                signal_type=SignalType.HOLD,
                symbol=symbol,
                price=current_price,
                reason="No EMA data",
                timestamp=now,
            )

        ema_str = ", ".join(f"EMA{p}={v:.0f}" for p, v in sorted(ema_vals.items()))
        print(f"  MA收斂: spread={spread_pct:.3f}% | {ema_str}")

        if not was_converged:
            return StrategySignal(
                signal_type=SignalType.HOLD,
                symbol=symbol,
                price=current_price,
                reason=f"No recent convergence (spread={spread_pct:.3f}%)",
                timestamp=now,
            )

        # Breakout long
        if breakout_long and spread_expanding:
            if self.volume_confirm:
                vol_pct = self.indicator_service.volume_percentile(
                    kbar_list, self.volume_percentile_lookback
                )
                if vol_pct is not None and vol_pct < self.volume_percentile_threshold:
                    return StrategySignal(
                        signal_type=SignalType.HOLD,
                        symbol=symbol,
                        price=current_price,
                        reason=f"MA breakout long but low volume ({vol_pct:.0%})",
                        timestamp=now,
                    )

            self._had_position = True
            self._bars_since_entry = 0

            return StrategySignal(
                signal_type=SignalType.ENTRY_LONG,
                symbol=symbol,
                price=current_price,
                confidence=0.8,
                reason=(
                    f"MA Convergence Breakout Long: "
                    f"spread={spread_pct:.3f}%, price>{max(ema_vals.values()):.0f}"
                ),
                timestamp=now,
                metadata={
                    "ema_values": ema_vals,
                    "spread_pct": spread_pct,
                },
            )

        # Breakout short
        if not self.long_only and breakout_short and spread_expanding:
            if self.volume_confirm:
                vol_pct = self.indicator_service.volume_percentile(
                    kbar_list, self.volume_percentile_lookback
                )
                if vol_pct is not None and vol_pct < self.volume_percentile_threshold:
                    return StrategySignal(
                        signal_type=SignalType.HOLD,
                        symbol=symbol,
                        price=current_price,
                        reason=f"MA breakout short but low volume ({vol_pct:.0%})",
                        timestamp=now,
                    )

            self._had_position = True
            self._bars_since_entry = 0

            return StrategySignal(
                signal_type=SignalType.ENTRY_SHORT,
                symbol=symbol,
                price=current_price,
                confidence=0.8,
                reason=(
                    f"MA Convergence Breakout Short: "
                    f"spread={spread_pct:.3f}%, price<{min(ema_vals.values()):.0f}"
                ),
                timestamp=now,
                metadata={
                    "ema_values": ema_vals,
                    "spread_pct": spread_pct,
                },
            )

        return StrategySignal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            price=current_price,
            reason=(
                f"Converged but no breakout yet "
                f"(long={breakout_long}, short={breakout_short}, "
                f"expanding={spread_expanding})"
            ),
            timestamp=now,
        )
