"""Position Manager - 倉位管理器

負責管理交易部位的生命週期：
- 接收策略信號，決定開倉數量和 Leg 分配
- 追蹤價格變化，管理每個 Leg 的停損/停利/移動停損
- 產生 OrderAction 給 Executor 執行

支援做多和做空：
  無倉位 → 收到 ENTRY_LONG  → 開多倉（建立多個 Legs）
  無倉位 → 收到 ENTRY_SHORT → 開空倉（建立多個 Legs）
  持倉中 → 價格更新 → 檢查每個 Leg 的出場條件 → 產生平倉指令
"""

import uuid
from datetime import datetime

from auto_trade.models.account import Action
from auto_trade.models.market import KBarList
from auto_trade.models.position import (
    ExitRule,
    LegType,
    ManagedPosition,
    OrderAction,
    PositionLeg,
    PositionStatus,
)
from auto_trade.models.position_record import ExitReason
from auto_trade.models.strategy import SignalType, StrategySignal
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.utils import calculate_points


class PositionManagerConfig:
    """PositionManager 配置

    定義倉位管理的所有參數：
    - 下單數量和 Leg 分配
    - 停損/停利/移動停損的設定
    - 收緊移停（Staged Trailing Stop）設定
    """

    def __init__(
        self,
        # 倉位分配
        total_quantity: int = 4,
        tp_leg_quantity: int = 2,  # TP Leg 數量
        ts_leg_quantity: int = 2,  # TS Leg 數量
        # 停損設定
        stop_loss_points: int = 50,
        stop_loss_points_rate: float | None = None,
        # 停利設定（TP Leg）
        take_profit_points: int = 500,
        take_profit_points_rate: float | None = None,
        # 移動停損設定（TS Leg）
        start_trailing_stop_points: int = 200,
        trailing_stop_points: int = 200,
        trailing_stop_points_rate: float | None = None,
        # 收緊移停（Staged Trailing Stop）
        # 當獲利達到 tighten_after_points 後，移停距離從 trailing_stop_points 縮小到 tightened_trailing_stop_points
        tighten_after_points: int | None = None,
        tighten_after_points_rate: float | None = None,
        tightened_trailing_stop_points: int | None = None,
        tightened_trailing_stop_points_rate: float | None = None,
        # 其他
        timeframe: str = "30m",
        enable_macd_fast_stop: bool = True,
        # 時間強制平倉（日內策略用，格式 "HH:MM"，如 "13:30"）
        force_exit_time: str | None = None,
    ):
        self.total_quantity = total_quantity
        self.tp_leg_quantity = tp_leg_quantity
        self.ts_leg_quantity = ts_leg_quantity

        # 驗證數量分配
        if tp_leg_quantity + ts_leg_quantity != total_quantity:
            raise ValueError(
                f"Leg 數量不一致: tp({tp_leg_quantity}) + ts({ts_leg_quantity}) != total({total_quantity})"
            )

        self.stop_loss_points = stop_loss_points
        self.stop_loss_points_rate = stop_loss_points_rate
        self.take_profit_points = take_profit_points
        self.take_profit_points_rate = take_profit_points_rate
        self.start_trailing_stop_points = start_trailing_stop_points
        self.trailing_stop_points = trailing_stop_points
        self.trailing_stop_points_rate = trailing_stop_points_rate

        # 收緊移停
        self.tighten_after_points = tighten_after_points
        self.tighten_after_points_rate = tighten_after_points_rate
        self.tightened_trailing_stop_points = tightened_trailing_stop_points
        self.tightened_trailing_stop_points_rate = tightened_trailing_stop_points_rate

        self.timeframe = timeframe
        self.enable_macd_fast_stop = enable_macd_fast_stop
        self.force_exit_time = force_exit_time

    @classmethod
    def from_dict(
        cls, trading: dict, position: dict | None = None
    ) -> "PositionManagerConfig":
        """從 YAML 字典直接建立 PositionManagerConfig

        新增參數時，只需在這裡加一行 .get()，
        main.py / run_backtest.py 完全不需要改。

        Args:
            trading: YAML 中的 trading 區塊
            position: YAML 中的 position 區塊（可選）

        Returns:
            PositionManagerConfig 實例
        """
        pos = position or {}
        total_qty = pos.get("total_quantity", 1)
        return cls(
            # 倉位分配
            total_quantity=total_qty,
            tp_leg_quantity=pos.get("tp_leg_quantity", 0),
            ts_leg_quantity=pos.get("ts_leg_quantity", total_qty),
            # 停損
            stop_loss_points=trading["stop_loss_points"],
            stop_loss_points_rate=trading.get("stop_loss_points_rate"),
            # 停利
            take_profit_points=trading["take_profit_points"],
            take_profit_points_rate=trading.get("take_profit_points_rate"),
            # 移動停損
            start_trailing_stop_points=trading["start_trailing_stop_points"],
            trailing_stop_points=trading["trailing_stop_points"],
            trailing_stop_points_rate=trading.get("trailing_stop_points_rate"),
            # 收緊移停
            tighten_after_points=trading.get("tighten_after_points"),
            tighten_after_points_rate=trading.get("tighten_after_points_rate"),
            tightened_trailing_stop_points=trading.get(
                "tightened_trailing_stop_points"
            ),
            tightened_trailing_stop_points_rate=trading.get(
                "tightened_trailing_stop_points_rate"
            ),
            # 其他
            timeframe=trading.get("timeframe", "30m"),
            enable_macd_fast_stop=trading.get("enable_macd_fast_stop", True),
            force_exit_time=trading.get("force_exit_time"),
        )

    @property
    def has_tightened_trailing_stop(self) -> bool:
        """是否有設定收緊移停"""
        return (
            self.tighten_after_points is not None
            or self.tighten_after_points_rate is not None
        ) and (
            self.tightened_trailing_stop_points is not None
            or self.tightened_trailing_stop_points_rate is not None
        )

    def __repr__(self) -> str:
        parts = (
            f"PositionManagerConfig("
            f"total={self.total_quantity}, "
            f"tp_legs={self.tp_leg_quantity}, "
            f"ts_legs={self.ts_leg_quantity}, "
            f"SL={self.stop_loss_points}, "
            f"TP={self.take_profit_points}, "
            f"TS_start={self.start_trailing_stop_points}, "
            f"TS={self.trailing_stop_points}"
        )
        if self.has_tightened_trailing_stop:
            parts += f", tighten@{self.tighten_after_points}→{self.tightened_trailing_stop_points}"
        if self.force_exit_time:
            parts += f", force_exit@{self.force_exit_time}"
        parts += ")"
        return parts


class PositionManager:
    """倉位管理器

    管理一個 ManagedPosition 的完整生命週期。
    接收市場資料，產生 OrderAction。
    支援做多（Buy）和做空（Sell）方向。
    """

    def __init__(
        self,
        config: PositionManagerConfig,
        indicator_service: IndicatorService | None = None,
    ):
        self.config = config
        self.indicator_service = indicator_service
        self.position: ManagedPosition | None = None

        # MACD 快速停損相關
        self._last_fast_stop_check_kbar_time: datetime | None = None

    @property
    def has_position(self) -> bool:
        """是否有倉位"""
        return (
            self.position is not None and self.position.status != PositionStatus.CLOSED
        )

    @property
    def _is_long(self) -> bool:
        """當前倉位是否為做多"""
        return self.position is not None and self.position.direction == Action.Buy

    @property
    def _close_action(self) -> Action:
        """平倉動作方向（做多用 Sell 平倉，做空用 Buy 平倉）"""
        return Action.Sell if self._is_long else Action.Buy

    def on_signal(
        self,
        signal: StrategySignal,
        kbar_list: KBarList,
        symbol: str,
        sub_symbol: str,
    ) -> list[OrderAction]:
        """處理策略信號

        Args:
            signal: 策略產生的信號
            kbar_list: 當前 K 線資料（用於計算停損）
            symbol: 商品代碼
            sub_symbol: 子商品代碼

        Returns:
            list[OrderAction]: 需要執行的下單動作列表
        """
        if signal.signal_type == SignalType.ENTRY_LONG and not self.has_position:
            return self._open_position(
                signal, kbar_list, symbol, sub_symbol, Action.Buy
            )

        if signal.signal_type == SignalType.ENTRY_SHORT and not self.has_position:
            return self._open_position(
                signal, kbar_list, symbol, sub_symbol, Action.Sell
            )

        return []

    def on_price_update(
        self,
        current_price: int,
        kbar_list: KBarList | None = None,
    ) -> list[OrderAction]:
        """處理價格更新

        檢查所有 Leg 的出場條件，產生平倉指令。

        Args:
            current_price: 當前價格
            kbar_list: 當前 K 線資料（用於 MACD 快速停損）

        Returns:
            list[OrderAction]: 需要執行的平倉動作列表
        """
        if not self.has_position:
            return []

        # 更新價格追蹤
        self.position.update_price_tracking(current_price)

        actions: list[OrderAction] = []

        # 檢查 MACD 快速停損（整個 Position 級別）
        if (
            self.config.enable_macd_fast_stop
            and kbar_list is not None
            and self.indicator_service is not None
        ):
            fast_stop_triggered = self._check_macd_fast_stop(current_price, kbar_list)
            if fast_stop_triggered:
                # 快速停損觸發：關閉所有 open legs
                actions.extend(
                    self._close_all_legs(current_price, ExitReason.FAST_STOP)
                )
                return actions

        # 檢查動能衰竭停利（整個 Position 級別）
        if (
            kbar_list is not None
            and self.indicator_service is not None
            and self._check_momentum_exhaustion(current_price, kbar_list)
        ):
            actions.extend(
                self._close_all_legs(
                    current_price, ExitReason.MOMENTUM_EXIT
                )
            )
            return actions

        # 逐 Leg 檢查出場條件
        for leg in self.position.open_legs:
            action = self._check_leg_exit(leg, current_price)
            if action:
                actions.append(action)

        # 更新移動停損（所有 Legs）
        self._update_trailing_stops(current_price)

        return actions

    def on_fill(
        self,
        leg_id: str,
        fill_price: int,
        fill_time: datetime,
        exit_reason: ExitReason,
    ) -> None:
        """處理成交回報

        Args:
            leg_id: 成交的 Leg ID
            fill_price: 成交價格
            fill_time: 成交時間
            exit_reason: 出場原因
        """
        if self.position:
            self.position.close_leg(leg_id, fill_price, fill_time, exit_reason)

            # 如果所有 Leg 都已平倉，清除 position
            if self.position.status == PositionStatus.CLOSED:
                print(f"📦 Position {self.position.position_id} 已完全平倉")
                self.position = None
                self._last_fast_stop_check_kbar_time = None

    def check_time_exit(
        self, current_time: datetime, current_price: int
    ) -> list[OrderAction]:
        """檢查時間強制平倉

        如果設定了 force_exit_time，且當前時間已達或超過，則強制平倉所有 Legs。
        用於日內策略（如 ORB）在收盤前平倉。

        Args:
            current_time: 當前時間
            current_price: 當前價格（用於計算 PnL）

        Returns:
            list[OrderAction]: 平倉指令列表（空表示不需要平倉）
        """
        if not self.has_position or not self.config.force_exit_time:
            return []

        exit_h, exit_m = map(int, self.config.force_exit_time.split(":"))
        cur_h, cur_m = current_time.hour, current_time.minute

        if cur_h > exit_h or (cur_h == exit_h and cur_m >= exit_m):
            print(
                f"⏰ 時間強制平倉: {current_time.strftime('%H:%M')} >= "
                f"{self.config.force_exit_time}，平倉價 {current_price}"
            )
            return self._close_all_legs(current_price, ExitReason.TIME_EXIT)

        return []

    def reset(self) -> None:
        """重置 PositionManager 狀態"""
        self.position = None
        self._last_fast_stop_check_kbar_time = None

    # === Private Methods ===

    def _open_position(
        self,
        signal: StrategySignal,
        kbar_list: KBarList,
        symbol: str,
        sub_symbol: str,
        direction: Action,
    ) -> list[OrderAction]:
        """建立倉位（做多或做空），分配 Legs

        Args:
            signal: 策略信號
            kbar_list: K線資料
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            direction: 交易方向 (Buy=做多, Sell=做空)
        """
        entry_price = int(signal.price)
        is_long = direction == Action.Buy
        meta = signal.metadata or {}

        # === 計算停損價格（支援 metadata 覆寫）===
        if "override_stop_loss_price" in meta:
            stop_loss_price = int(meta["override_stop_loss_price"])
        elif "override_stop_loss_distance" in meta:
            sl_dist = int(meta["override_stop_loss_distance"])
            stop_loss_price = (
                entry_price - sl_dist if is_long else entry_price + sl_dist
            )
        else:
            stop_loss_price = self._calculate_initial_stop_loss(
                kbar_list, entry_price, direction
            )

        # === 計算停利價格（支援 metadata 覆寫）===
        if "override_take_profit_points" in meta:
            tp_pts = int(meta["override_take_profit_points"])
        else:
            tp_pts = calculate_points(
                self.config.take_profit_points,
                self.config.take_profit_points_rate,
                entry_price,
            )
        take_profit_price = entry_price + tp_pts if is_long else entry_price - tp_pts

        # === 計算啟動移動停損價格（支援 metadata 覆寫）===
        if "override_start_trailing_stop_points" in meta:
            start_ts_pts = int(meta["override_start_trailing_stop_points"])
        else:
            start_ts_pts = self.config.start_trailing_stop_points
        start_trailing_stop_price = (
            entry_price + start_ts_pts if is_long else entry_price - start_ts_pts
        )

        # 建立 Position
        position_id = str(uuid.uuid4())[:8]
        legs: list[PositionLeg] = []

        # 計算收緊移停參數
        tighten_after_price: int | None = None
        tightened_ts_points: int | None = None
        if self.config.has_tightened_trailing_stop:
            tighten_after_pts = calculate_points(
                self.config.tighten_after_points,
                self.config.tighten_after_points_rate,
                entry_price,
            )
            tighten_after_price = (
                entry_price + tighten_after_pts
                if is_long
                else entry_price - tighten_after_pts
            )
            tightened_ts_points = calculate_points(
                self.config.tightened_trailing_stop_points,
                self.config.tightened_trailing_stop_points_rate,
                entry_price,
            )

        # 建立 TP Legs（有停利目標 + 移動停損保護）
        if self.config.tp_leg_quantity > 0:
            tp_leg = PositionLeg(
                leg_id=f"{position_id}-TP",
                leg_type=LegType.TAKE_PROFIT,
                quantity=self.config.tp_leg_quantity,
                exit_rule=ExitRule(
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    start_trailing_stop_price=start_trailing_stop_price,
                    tighten_after_price=tighten_after_price,
                    tightened_trailing_stop_points=tightened_ts_points,
                ),
            )
            legs.append(tp_leg)

        # 建立 TS Legs（純移動停損，沒有停利上限）
        if self.config.ts_leg_quantity > 0:
            ts_leg = PositionLeg(
                leg_id=f"{position_id}-TS",
                leg_type=LegType.TRAILING_STOP,
                quantity=self.config.ts_leg_quantity,
                exit_rule=ExitRule(
                    stop_loss_price=stop_loss_price,
                    start_trailing_stop_price=start_trailing_stop_price,
                    tighten_after_price=tighten_after_price,
                    tightened_trailing_stop_points=tightened_ts_points,
                ),
            )
            legs.append(ts_leg)

        # 將 metadata override 保存在 position 上（供 trailing stop 等使用）
        position_metadata: dict = {}
        if "override_trailing_stop_points" in meta:
            position_metadata["override_trailing_stop_points"] = int(
                meta["override_trailing_stop_points"]
            )

        # 階梯式壓力線移停：保存關鍵價位到 position metadata
        if "key_levels" in meta:
            position_metadata["key_levels"] = meta["key_levels"]
            position_metadata["key_level_buffer"] = meta.get(
                "key_level_buffer", 10
            )
            position_metadata["next_key_level_idx"] = 0
            if "key_level_min_profit" in meta:
                position_metadata["key_level_min_profit"] = meta[
                    "key_level_min_profit"
                ]
            extras = []
            if meta.get("key_level_min_profit"):
                extras.append(f"min_profit={meta['key_level_min_profit']}pts")
            print(
                f"  🔑 Key level trailing: levels={meta['key_levels']}, "
                f"buffer={meta.get('key_level_buffer', 10)}pts"
                + (f", {', '.join(extras)}" if extras else "")
            )

        # 動能衰竭停利參數
        if meta.get("use_momentum_exit"):
            position_metadata["use_momentum_exit"] = True
            position_metadata["momentum_min_profit"] = meta.get(
                "momentum_min_profit", 0
            )
            position_metadata["momentum_lookback"] = meta.get(
                "momentum_lookback", 5
            )
            position_metadata["momentum_weak_threshold"] = meta.get(
                "momentum_weak_threshold", 0.45
            )
            position_metadata["momentum_min_weak_bars"] = meta.get(
                "momentum_min_weak_bars", 3
            )
            print(
                f"  🔍 Momentum exit: lookback={meta.get('momentum_lookback', 5)}, "
                f"min_profit={meta.get('momentum_min_profit', 0)}pts, "
                f"weak<{meta.get('momentum_weak_threshold', 0.45)}, "
                f"min_weak={meta.get('momentum_min_weak_bars', 3)}bars"
            )

        self.position = ManagedPosition(
            position_id=position_id,
            symbol=symbol,
            sub_symbol=sub_symbol,
            direction=direction,
            total_quantity=self.config.total_quantity,
            entry_price=entry_price,
            entry_time=datetime.now(),
            legs=legs,
            highest_price=entry_price,
            lowest_price=entry_price,
            metadata=position_metadata,
        )

        dir_str = "📈 做多" if is_long else "📉 做空"
        tighten_info = ""
        if tighten_after_price is not None:
            tighten_info = f", 收緊移停@{tighten_after_price}→{tightened_ts_points}pts"
        print(
            f"{dir_str} 建立倉位 {position_id}: "
            f"入場 {entry_price}, "
            f"停損 {stop_loss_price}, "
            f"停利 {take_profit_price}, "
            f"啟動移停 {start_trailing_stop_price}, "
            f"TP×{self.config.tp_leg_quantity} + TS×{self.config.ts_leg_quantity}"
            f"{tighten_info}"
        )

        # 產生開倉指令
        return [
            OrderAction(
                action=direction,
                symbol=symbol,
                sub_symbol=sub_symbol,
                quantity=self.config.total_quantity,
                order_type="Open",
                reason=signal.reason,
            )
        ]

    def _calculate_initial_stop_loss(
        self, kbar_list: KBarList, entry_price: int, direction: Action
    ) -> int:
        """計算初始停損價格

        做多：前 30 根 K 棒最低點 - 停損點數（停損在下方）
        做空：前 30 根 K 棒最高點 + 停損點數（停損在上方）
        """
        stop_loss_points = calculate_points(
            self.config.stop_loss_points,
            self.config.stop_loss_points_rate,
            entry_price,
        )

        is_long = direction == Action.Buy

        if len(kbar_list) >= 31:
            try:
                if is_long:
                    extreme_price = int(min(kbar.low for kbar in kbar_list.kbars[-31:]))
                    return extreme_price - stop_loss_points
                else:
                    extreme_price = int(
                        max(kbar.high for kbar in kbar_list.kbars[-31:])
                    )
                    return extreme_price + stop_loss_points
            except Exception:
                pass

        if is_long:
            return entry_price - stop_loss_points
        else:
            return entry_price + stop_loss_points

    def _check_leg_exit(
        self, leg: PositionLeg, current_price: int
    ) -> OrderAction | None:
        """檢查單一 Leg 的出場條件（方向感知）"""
        if not self.position or leg.status != PositionStatus.OPEN:
            return None

        exit_rule = leg.exit_rule
        is_long = self._is_long

        # 1. 檢查停損（所有 Leg 共用）
        #    做多：價格跌破停損 → 出場
        #    做空：價格漲破停損 → 出場
        if exit_rule.stop_loss_price is not None:
            sl_hit = (
                current_price <= exit_rule.stop_loss_price
                if is_long
                else current_price >= exit_rule.stop_loss_price
            )
            if sl_hit:
                exit_reason = ExitReason.STOP_LOSS
                print(
                    f"🔴 {leg.leg_id} 觸發停損: "
                    f"價格 {current_price} {'<=' if is_long else '>='} "
                    f"{exit_rule.stop_loss_price}"
                )
                return OrderAction(
                    action=self._close_action,
                    symbol=self.position.symbol,
                    sub_symbol=self.position.sub_symbol,
                    quantity=leg.quantity,
                    order_type="Close",
                    reason=f"{leg.leg_type.value} Stop Loss",
                    leg_id=leg.leg_id,
                    metadata={
                        "exit_reason": exit_reason.value,
                        "trigger_price": exit_rule.stop_loss_price,
                    },
                )

        # 2. 檢查移動停損（所有 Leg，已啟動時檢查）
        #    做多：價格跌破移停價 → 出場
        #    做空：價格漲破移停價 → 出場
        if exit_rule.trailing_stop_active and exit_rule.trailing_stop_price is not None:
            ts_hit = (
                current_price <= exit_rule.trailing_stop_price
                if is_long
                else current_price >= exit_rule.trailing_stop_price
            )
            if ts_hit:
                exit_reason = ExitReason.TRAILING_STOP
                print(
                    f"🟡 {leg.leg_id} 觸發移動停損: "
                    f"價格 {current_price} {'<=' if is_long else '>='} "
                    f"{exit_rule.trailing_stop_price}"
                )
                return OrderAction(
                    action=self._close_action,
                    symbol=self.position.symbol,
                    sub_symbol=self.position.sub_symbol,
                    quantity=leg.quantity,
                    order_type="Close",
                    reason="Trailing Stop",
                    leg_id=leg.leg_id,
                    metadata={
                        "exit_reason": exit_reason.value,
                        "trigger_price": exit_rule.trailing_stop_price,
                    },
                )

        # 3. 檢查停利（僅 TP Leg）
        #    做多：價格漲到停利價 → 出場
        #    做空：價格跌到停利價 → 出場
        if (
            leg.leg_type == LegType.TAKE_PROFIT
            and exit_rule.take_profit_price is not None
        ):
            tp_hit = (
                current_price >= exit_rule.take_profit_price
                if is_long
                else current_price <= exit_rule.take_profit_price
            )
            if tp_hit:
                exit_reason = ExitReason.TAKE_PROFIT
                print(
                    f"🟢 {leg.leg_id} 觸發停利: "
                    f"價格 {current_price} {'>=' if is_long else '<='} "
                    f"{exit_rule.take_profit_price}"
                )
                return OrderAction(
                    action=self._close_action,
                    symbol=self.position.symbol,
                    sub_symbol=self.position.sub_symbol,
                    quantity=leg.quantity,
                    order_type="Close",
                    reason="Take Profit",
                    leg_id=leg.leg_id,
                    metadata={
                        "exit_reason": exit_reason.value,
                        "trigger_price": exit_rule.take_profit_price,
                    },
                )

        return None

    def _get_trailing_stop_points(self, exit_rule: ExitRule) -> int:
        """根據收緊狀態取得當前的移停距離

        如果已收緊（is_tightened=True），使用較小的 tightened 距離；
        否則使用原始的 trailing_stop_points。
        支援 position metadata 中的 override_trailing_stop_points 覆寫。
        """
        if (
            exit_rule.is_tightened
            and exit_rule.tightened_trailing_stop_points is not None
        ):
            return exit_rule.tightened_trailing_stop_points

        # 檢查 position metadata 中是否有 override
        if (
            self.position
            and self.position.metadata.get("override_trailing_stop_points") is not None
        ):
            return int(self.position.metadata["override_trailing_stop_points"])

        return calculate_points(
            self.config.trailing_stop_points,
            self.config.trailing_stop_points_rate,
            self.position.entry_price,
        )

    def _update_trailing_stops(self, current_price: int) -> None:
        """更新所有 Legs 的移動停損（方向感知）

        做多：價格上漲到啟動價 → 啟動；移停價 = 當前價 - 點數（只往上調）
        做空：價格下跌到啟動價 → 啟動；移停價 = 當前價 + 點數（只往下調）

        階梯式壓力線移停：當有 key_levels 時，突破壓力線後將移停設在該壓力線。
        所有壓力線都突破後，回歸固定移停。

        收緊移停：當獲利到達 tighten_after_price 後，移停距離自動縮小。
        """
        if not self.position:
            return

        is_long = self._is_long

        # === 階梯式壓力線移停 ===
        key_levels = self.position.metadata.get("key_levels")
        if key_levels is not None:
            idx = self.position.metadata.get("next_key_level_idx", 0)
            buffer = self.position.metadata.get("key_level_buffer", 10)
            min_profit = self.position.metadata.get("key_level_min_profit", 0)

            # 最低獲利門檻：未達門檻前不啟用壓力線移停，回歸固定移停
            if min_profit > 0:
                unrealized = (
                    current_price - self.position.entry_price
                    if is_long
                    else self.position.entry_price - current_price
                )
                if unrealized < min_profit:
                    key_levels = None  # 暫時跳過壓力線模式

        if key_levels is not None:
            idx = self.position.metadata.get("next_key_level_idx", 0)
            buffer = self.position.metadata.get("key_level_buffer", 10)

            # 檢查是否突破了新的壓力/支撐線
            while idx < len(key_levels):
                next_level = key_levels[idx]
                crossed = (
                    current_price > next_level
                    if is_long
                    else current_price < next_level
                )
                if crossed:
                    stop_price = (
                        next_level - buffer
                        if is_long
                        else next_level + buffer
                    )
                    for leg in self.position.open_legs:
                        leg.exit_rule.trailing_stop_active = True
                        if is_long and (
                            leg.exit_rule.trailing_stop_price is None
                            or stop_price > leg.exit_rule.trailing_stop_price
                        ):
                            leg.exit_rule.trailing_stop_price = stop_price
                        elif not is_long and (
                            leg.exit_rule.trailing_stop_price is None
                            or stop_price < leg.exit_rule.trailing_stop_price
                        ):
                            leg.exit_rule.trailing_stop_price = stop_price
                    idx += 1
                    print(
                        f"📊 Key level broken: {next_level}, "
                        f"stop → {stop_price} "
                        f"({idx}/{len(key_levels)} levels)"
                    )
                else:
                    break

            self.position.metadata["next_key_level_idx"] = idx

            if idx < len(key_levels):
                # 仍有未突破的壓力線 → 維持壓力線模式，不用固定移停
                return

            # 所有壓力線都已突破 → 用開倉價 × 0.005 作為移停距離
            entry = self.position.entry_price
            dynamic_ts = int(entry * 0.005)
            for leg in self.position.open_legs:
                er = leg.exit_rule
                if not er.trailing_stop_active:
                    continue
                new_stop = (
                    current_price - dynamic_ts
                    if is_long
                    else current_price + dynamic_ts
                )
                if is_long and (
                    er.trailing_stop_price is None
                    or new_stop > er.trailing_stop_price
                ):
                    er.trailing_stop_price = new_stop
                    print(
                        f"📊 {leg.leg_id} 壓力線後移停更新: "
                        f"{new_stop} (距離={dynamic_ts}pts, 0.5%)"
                    )
                elif not is_long and (
                    er.trailing_stop_price is None
                    or new_stop < er.trailing_stop_price
                ):
                    er.trailing_stop_price = new_stop
                    print(
                        f"📊 {leg.leg_id} 壓力線後移停更新: "
                        f"{new_stop} (距離={dynamic_ts}pts, 0.5%)"
                    )
            return

        for leg in self.position.open_legs:
            exit_rule = leg.exit_rule

            # 沒有設定 start_trailing_stop_price 的 leg 跳過
            if exit_rule.start_trailing_stop_price is None:
                continue

            # 檢查是否啟動移動停損
            if not exit_rule.trailing_stop_active:
                should_activate = (
                    current_price >= exit_rule.start_trailing_stop_price
                    if is_long
                    else current_price <= exit_rule.start_trailing_stop_price
                )

                if should_activate:
                    exit_rule.trailing_stop_active = True
                    ts_points = self._get_trailing_stop_points(exit_rule)
                    exit_rule.trailing_stop_price = (
                        current_price - ts_points
                        if is_long
                        else current_price + ts_points
                    )
                    print(
                        f"✅ {leg.leg_id} 移動停損啟動: "
                        f"價格 {current_price} "
                        f"{'>=' if is_long else '<='} "
                        f"{exit_rule.start_trailing_stop_price}, "
                        f"移停價 {exit_rule.trailing_stop_price}"
                    )
            else:
                # 檢查是否應該收緊移停
                if (
                    not exit_rule.is_tightened
                    and exit_rule.tighten_after_price is not None
                    and exit_rule.tightened_trailing_stop_points is not None
                ):
                    should_tighten = (
                        current_price >= exit_rule.tighten_after_price
                        if is_long
                        else current_price <= exit_rule.tighten_after_price
                    )
                    if should_tighten:
                        exit_rule.is_tightened = True
                        # 立即用收緊距離更新移停價
                        new_stop_price = (
                            current_price - exit_rule.tightened_trailing_stop_points
                            if is_long
                            else current_price
                            + exit_rule.tightened_trailing_stop_points
                        )
                        # 只允許收緊（做多只往上、做空只往下）
                        if (
                            is_long
                            and (
                                exit_rule.trailing_stop_price is None
                                or new_stop_price > exit_rule.trailing_stop_price
                            )
                            or not is_long
                            and (
                                exit_rule.trailing_stop_price is None
                                or new_stop_price < exit_rule.trailing_stop_price
                            )
                        ):
                            exit_rule.trailing_stop_price = new_stop_price
                        print(
                            f"🔧 {leg.leg_id} 移動停損收緊: "
                            f"價格 {current_price} "
                            f"{'>=' if is_long else '<='} "
                            f"{exit_rule.tighten_after_price}, "
                            f"距離 → {exit_rule.tightened_trailing_stop_points}pts, "
                            f"移停價 {exit_rule.trailing_stop_price}"
                        )
                        continue  # 收緊時已更新，跳過下方的常規更新

                # 已啟動，常規更新移動停損價格
                ts_points = self._get_trailing_stop_points(exit_rule)

                if is_long:
                    # 做多：移停價只往上調（追蹤最高價）
                    new_stop_price = current_price - ts_points
                    if (
                        exit_rule.trailing_stop_price is None
                        or new_stop_price > exit_rule.trailing_stop_price
                    ):
                        exit_rule.trailing_stop_price = new_stop_price
                        print(f"📊 {leg.leg_id} 移動停損更新: {new_stop_price}")
                else:
                    # 做空：移停價只往下調（追蹤最低價）
                    new_stop_price = current_price + ts_points
                    if (
                        exit_rule.trailing_stop_price is None
                        or new_stop_price < exit_rule.trailing_stop_price
                    ):
                        exit_rule.trailing_stop_price = new_stop_price
                        print(f"📊 {leg.leg_id} 移動停損更新: {new_stop_price}")

    def _close_all_legs(
        self, current_price: int, exit_reason: ExitReason
    ) -> list[OrderAction]:
        """關閉所有開放的 Legs"""
        if not self.position:
            return []

        actions = []
        total_close_quantity = 0

        for leg in self.position.open_legs:
            total_close_quantity += leg.quantity

        if total_close_quantity > 0:
            # 合併成一個平倉單
            actions.append(
                OrderAction(
                    action=self._close_action,
                    symbol=self.position.symbol,
                    sub_symbol=self.position.sub_symbol,
                    quantity=total_close_quantity,
                    order_type="Close",
                    reason=f"Close all: {exit_reason.value}",
                    metadata={
                        "exit_reason": exit_reason.value,
                        "leg_ids": [leg.leg_id for leg in self.position.open_legs],
                    },
                )
            )

        return actions

    def _check_macd_fast_stop(self, current_price: int, kbar_list: KBarList) -> bool:
        """檢查 MACD 快速停損（方向感知）

        做多：死叉為不利信號 → 虧損超過門檻則觸發
        做空：金叉為不利信號 → 虧損超過門檻則觸發
        """
        if not self.position or not self.indicator_service:
            return False

        is_long = self._is_long

        # 計算當前盈虧（方向感知）
        current_profit = (
            current_price - self.position.entry_price
            if is_long
            else self.position.entry_price - current_price
        )

        stop_loss_threshold = calculate_points(
            self.config.stop_loss_points,
            self.config.stop_loss_points_rate,
            self.position.entry_price,
        )

        if len(kbar_list) < 35:
            return False

        # 獲取最新 K 棒的時間
        latest_kbar = kbar_list.kbars[-1]
        latest_kbar_time = latest_kbar.time

        # 如果是同一根 K 棒，不重複檢查
        if self._last_fast_stop_check_kbar_time == latest_kbar_time:
            return False

        self._last_fast_stop_check_kbar_time = latest_kbar_time

        # 檢查是否有任何 Leg 已啟動移停（已啟動則不使用快速停損）
        any_trailing_active = any(
            leg.exit_rule.trailing_stop_active for leg in self.position.open_legs
        )

        # 如果已經在不利交叉狀態且虧損達標
        if (
            self.position.is_in_macd_adverse_cross
            and not any_trailing_active
            and current_profit < -stop_loss_threshold
        ):
            print(
                f"⚡ MACD 快速停損觸發！虧損 {-current_profit} 點 >= 門檻 {stop_loss_threshold} 點"
            )
            return True

        # 計算 MACD 並檢查交叉
        macd_list = self.indicator_service.calculate_macd(kbar_list)
        is_death_cross = self.indicator_service.check_death_cross(
            macd_list, min_acceleration=None
        )
        is_golden_cross = self.indicator_service.check_golden_cross(macd_list)

        # 判斷不利交叉和有利交叉（方向感知）
        if is_long:
            is_adverse_cross = is_death_cross
            is_favorable_cross = is_golden_cross
        else:
            is_adverse_cross = is_golden_cross
            is_favorable_cross = is_death_cross

        if is_adverse_cross:
            self.position.is_in_macd_adverse_cross = True
            cross_name = "死叉" if is_long else "金叉"
            print(f"🔴 MACD {cross_name}確認（不利於{'多' if is_long else '空'}頭）")

            if not any_trailing_active and current_profit < -stop_loss_threshold:
                print(
                    f"⚡ MACD 快速停損觸發！虧損 {-current_profit} 點 >= 門檻 {stop_loss_threshold} 點"
                )
                return True

        elif is_favorable_cross:
            if self.position.is_in_macd_adverse_cross:
                self.position.is_in_macd_adverse_cross = False
                cross_name = "金叉" if is_long else "死叉"
                print(f"✅ MACD {cross_name}，解除不利交叉狀態")

        return False

    def _check_momentum_exhaustion(
        self, current_price: int, kbar_list: KBarList
    ) -> bool:
        """檢查動能衰竭停利

        在獲利達到門檻後，分析最近 N 根 K 棒是否呈現動能衰竭：
        1. 連續逆勢 K 棒（做多時出現偏空 K 棒）
        2. K 棒實體逐漸縮小（趨勢減速）
        滿足條件時觸發停利出場。
        """
        if not self.position or not self.indicator_service:
            return False

        meta = self.position.metadata
        if not meta.get("use_momentum_exit"):
            return False

        is_long = self._is_long

        # 獲利門檻檢查
        min_profit = meta.get("momentum_min_profit", 0)
        unrealized = (
            current_price - self.position.entry_price
            if is_long
            else self.position.entry_price - current_price
        )
        if unrealized < min_profit:
            return False

        # 取得最近 N 根 K 棒
        lookback = meta.get("momentum_lookback", 5)
        if len(kbar_list) < lookback + 2:
            return False

        latest_kbar = kbar_list.kbars[-1]
        latest_kbar_time = latest_kbar.time

        # 避免同根 K 棒重複檢查
        last_check = meta.get("_last_momentum_check_time")
        if last_check == latest_kbar_time:
            return False
        meta["_last_momentum_check_time"] = latest_kbar_time

        recent_bars = kbar_list.get_latest(lookback)
        weak_threshold = meta.get("momentum_weak_threshold", 0.45)

        # === 指標 1: 連續逆勢 K 棒數 ===
        consecutive_weak = 0
        for bar in reversed(recent_bars):
            strength = self.indicator_service.candle_strength(bar)
            if is_long and strength < weak_threshold:
                consecutive_weak += 1
            elif not is_long and strength > (1.0 - weak_threshold):
                consecutive_weak += 1
            else:
                break

        # === 指標 2: K 棒實體縮小（趨勢減速）===
        bodies = [abs(float(bar.close) - float(bar.open)) for bar in recent_bars]
        shrinking = 0
        for i in range(1, len(bodies)):
            if bodies[i] < bodies[i - 1] * 0.7:
                shrinking += 1

        # === 綜合判斷 ===
        min_weak_bars = meta.get("momentum_min_weak_bars", 3)

        # 條件 A: 連續逆勢 K 棒達標
        if consecutive_weak >= min_weak_bars:
            print(
                f"🔻 動能衰竭 (條件A): 連續 {consecutive_weak} 根逆勢K棒 "
                f"(門檻 {min_weak_bars}), 未實現獲利 {unrealized}pts"
            )
            return True

        # 條件 B: 多數 K 棒在縮小 + 至少有部分逆勢
        if shrinking >= lookback - 2 and consecutive_weak >= 2:
            print(
                f"🔻 動能衰竭 (條件B): {shrinking}/{lookback-1} 根縮量 + "
                f"{consecutive_weak} 根逆勢, 未實現獲利 {unrealized}pts"
            )
            return True

        return False
