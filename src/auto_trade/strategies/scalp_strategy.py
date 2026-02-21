"""Scalp Strategy - 日內極短線策略

簡單的突破/反轉偵測 + 固定點數獲利出場。
每天不限交易次數，適合高頻短線操作。

進場模式：
  - breakout : N 根 K 棒高低點突破
  - reversal : 連續同方向 K 棒後出現反轉
  - both     : 突破或反轉任一觸發即進場

出場：
  - 固定停利（預設 50 點）
  - 固定停損
  - 時間強制平倉（由 PM force_exit_time 處理）
"""

from __future__ import annotations

from datetime import time

from auto_trade.models.market import KBar, KBarList
from auto_trade.models.strategy import SignalType, StrategySignal
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.strategies.base_strategy import BaseStrategy


class ScalpStrategy(BaseStrategy):
    """日內極短線策略"""

    def __init__(
        self,
        indicator_service: IndicatorService,
        # --- 交易時段 ---
        session_start_time: str = "09:05",
        entry_end_time: str = "13:00",
        # --- 進場模式 ---
        entry_mode: str = "both",  # "breakout", "reversal", "both"
        # --- 突破參數 ---
        breakout_lookback: int = 12,
        breakout_min_strength: float = 0.6,
        # --- 反轉參數 ---
        reversal_consecutive: int = 3,
        reversal_min_strength: float = 0.65,
        # --- 方向 ---
        long_only: bool = False,
        short_only: bool = False,
        # --- 冷卻 ---
        cooldown_bars: int = 2,
        **kwargs,
    ):
        super().__init__(indicator_service, name="Scalp Strategy")

        self.session_start_time = self._parse_time(session_start_time)
        self.entry_end_time = self._parse_time(entry_end_time)

        self.entry_mode = entry_mode
        self.breakout_lookback = breakout_lookback
        self.breakout_min_strength = breakout_min_strength
        self.reversal_consecutive = reversal_consecutive
        self.reversal_min_strength = reversal_min_strength
        self.long_only = long_only
        self.short_only = short_only
        self.cooldown_bars = cooldown_bars

        # 內部狀態
        self._bars_since_last_exit = 999  # 大數初始化，確保一開始就能進場

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def evaluate(
        self,
        kbar_list: KBarList,
        current_price: float,
        symbol: str,
    ) -> StrategySignal:
        hold = StrategySignal(
            signal_type=SignalType.HOLD,
            symbol=symbol,
            price=current_price,
        )

        if len(kbar_list) < self.breakout_lookback + 2:
            return hold

        latest = kbar_list.kbars[-1]
        current_time = latest.time.time() if latest.time else None

        if current_time is None:
            return hold

        # 時段檢查
        if current_time < self.session_start_time:
            return hold
        if current_time >= self.entry_end_time:
            return hold

        # 冷卻檢查
        self._bars_since_last_exit += 1
        if self._bars_since_last_exit < self.cooldown_bars:
            return hold

        # 取得近期 K 棒
        lookback = max(self.breakout_lookback, self.reversal_consecutive + 1)
        recent = kbar_list.get_latest(lookback + 1)

        # 嘗試偵測進場信號
        signal = None

        if self.entry_mode in ("breakout", "both"):
            signal = self._check_breakout(recent, symbol, current_price)

        if signal is None and self.entry_mode in ("reversal", "both"):
            signal = self._check_reversal(recent, symbol, current_price)

        if signal is not None:
            return signal

        return hold

    def on_position_closed(self) -> None:
        """PM 平倉後呼叫，重設冷卻計數"""
        self._bars_since_last_exit = 0

    # ──────────────────────────────────────────────
    # Breakout Detection
    # ──────────────────────────────────────────────

    def _check_breakout(
        self,
        recent: list[KBar],
        symbol: str,
        current_price: float,
    ) -> StrategySignal | None:
        """N 根 K 棒高低點突破偵測"""
        if len(recent) < self.breakout_lookback + 1:
            return None

        current_bar = recent[-1]
        lookback_bars = recent[-(self.breakout_lookback + 1) : -1]

        highest = max(bar.high for bar in lookback_bars)
        lowest = min(bar.low for bar in lookback_bars)
        strength = self.indicator_service.candle_strength(current_bar)

        # 向上突破
        if (
            not self.short_only
            and current_bar.close > highest
            and strength >= self.breakout_min_strength
        ):
            print(
                f"  📊 Scalp 突破做多: close({current_bar.close}) > "
                f"highest({highest}), strength={strength:.2f}"
            )
            return StrategySignal(
                signal_type=SignalType.ENTRY_LONG,
                symbol=symbol,
                price=current_price,
                reason="Scalp breakout long",
                metadata={"entry_type": "breakout"},
            )

        # 向下突破
        if (
            not self.long_only
            and current_bar.close < lowest
            and strength <= (1.0 - self.breakout_min_strength)
        ):
            print(
                f"  📊 Scalp 突破做空: close({current_bar.close}) < "
                f"lowest({lowest}), strength={strength:.2f}"
            )
            return StrategySignal(
                signal_type=SignalType.ENTRY_SHORT,
                symbol=symbol,
                price=current_price,
                reason="Scalp breakout short",
                metadata={"entry_type": "breakout"},
            )

        return None

    # ──────────────────────────────────────────────
    # Reversal Detection
    # ──────────────────────────────────────────────

    def _check_reversal(
        self,
        recent: list[KBar],
        symbol: str,
        current_price: float,
    ) -> StrategySignal | None:
        """連續同向 K 棒後反轉偵測"""
        n = self.reversal_consecutive
        if len(recent) < n + 1:
            return None

        current_bar = recent[-1]
        prev_bars = recent[-(n + 1) : -1]

        current_strength = self.indicator_service.candle_strength(current_bar)

        # 反轉做多：前 N 根都是偏空（close < open），然後出現強陽線
        all_bearish = all(bar.close < bar.open for bar in prev_bars)
        if (
            not self.short_only
            and all_bearish
            and current_strength >= self.reversal_min_strength
            and current_bar.close > current_bar.open
        ):
            print(
                f"  📊 Scalp 反轉做多: {n}根連續偏空後 "
                f"strong bullish (strength={current_strength:.2f})"
            )
            return StrategySignal(
                signal_type=SignalType.ENTRY_LONG,
                symbol=symbol,
                price=current_price,
                reason="Scalp reversal long",
                metadata={"entry_type": "reversal"},
            )

        # 反轉做空：前 N 根都是偏多（close > open），然後出現強陰線
        all_bullish = all(bar.close > bar.open for bar in prev_bars)
        if (
            not self.long_only
            and all_bullish
            and current_strength <= (1.0 - self.reversal_min_strength)
            and current_bar.close < current_bar.open
        ):
            print(
                f"  📊 Scalp 反轉做空: {n}根連續偏多後 "
                f"strong bearish (strength={current_strength:.2f})"
            )
            return StrategySignal(
                signal_type=SignalType.ENTRY_SHORT,
                symbol=symbol,
                price=current_price,
                reason="Scalp reversal short",
                metadata={"entry_type": "reversal"},
            )

        return None

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _parse_time(t: str) -> time:
        parts = t.split(":")
        return time(int(parts[0]), int(parts[1]))

    def __repr__(self) -> str:
        parts = [f"mode={self.entry_mode}"]
        if self.entry_mode in ("breakout", "both"):
            parts.append(f"bk_lb={self.breakout_lookback}")
        if self.entry_mode in ("reversal", "both"):
            parts.append(f"rv_n={self.reversal_consecutive}")
        if self.long_only:
            parts.append("LongOnly")
        if self.short_only:
            parts.append("ShortOnly")
        parts.append(f"cd={self.cooldown_bars}")
        return f"Scalp({', '.join(parts)})"
