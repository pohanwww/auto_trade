"""Trading Engine - 實盤交易的薄協調者.

TradingEngine 不包含業務邏輯，只負責：
1. 協調 Strategy、PositionManager、Executor 之間的資料流
2. 管理主循環（獲取行情 → 策略評估 → PM 更新 → 執行下單）
3. I/O 相關操作（日誌、通知、持倉記錄）

所有交易邏輯分散在：
- Strategy → 信號產生
- PositionManager → 倉位決策
- Executor → 下單執行
"""

import json
import time as _time
from datetime import datetime

from auto_trade.executors.base_executor import BaseExecutor
from auto_trade.models.account import Action
from auto_trade.models.position import OrderAction
from auto_trade.models.position_record import ExitReason, PositionRecord
from auto_trade.models.trading_unit import TradingUnit
from auto_trade.services.account_service import AccountService
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.services.line_bot_service import LineBotService
from auto_trade.services.market_service import MarketService
from auto_trade.services.position_manager import PositionManager
from auto_trade.services.record_service import RecordService
from auto_trade.utils import (
    calculate_and_wait_to_next_execution,
    wait_seconds,
)


class TradingEngine:
    """實盤交易引擎

    薄的協調層，將以下組件串聯：
    - TradingUnit (Strategy + PM Config)
    - MarketService (行情資料)
    - Executor (下單執行)
    - RecordService (持倉記錄)
    - LineBotService (通知，可選)
    """

    def __init__(
        self,
        trading_unit: TradingUnit,
        market_service: MarketService,
        executor: BaseExecutor,
        indicator_service: IndicatorService,
        account_service: AccountService | None = None,
        record_service: RecordService | None = None,
        line_bot_service: LineBotService | None = None,
    ):
        self.trading_unit = trading_unit
        self.market_service = market_service
        self.executor = executor
        self.indicator_service = indicator_service
        self.account_service = account_service
        self.record_service = record_service or RecordService()
        self.line_bot_service = line_bot_service

        # 建立 PositionManager
        self.position_manager = PositionManager(
            config=trading_unit.pm_config,
            indicator_service=indicator_service,
        )

        # 交易商品信息（由 configure 設定）
        self.symbol: str | None = None
        self.sub_symbol: str | None = None

        # 檢測頻率
        self.signal_check_interval: int = 5  # 分鐘
        self.position_check_interval: int = 5  # 秒

        # 加碼信號去重
        self._addon_checked_this_interval: bool = False

        # 配置檔名（用於 dashboard 辨識程序）
        self.config_file: str | None = None

    def configure(
        self,
        symbol: str,
        sub_symbol: str,
        signal_check_interval: int = 5,
        position_check_interval: int = 5,
        config_file: str | None = None,
    ) -> None:
        """設定交易參數

        Args:
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            signal_check_interval: 信號檢測間隔（分鐘）
            position_check_interval: 持倉檢測間隔（秒）
            config_file: YAML 配置檔名（如 strategy_ma.yaml）
        """
        self.config_file = config_file
        self.symbol = symbol
        self.sub_symbol = sub_symbol
        self.signal_check_interval = signal_check_interval
        self.position_check_interval = position_check_interval

        print("🔧 TradingEngine 配置:")
        print(f"  交易單元: {self.trading_unit.name}")
        print(f"  策略: {self.trading_unit.strategy.name}")
        print(f"  倉位配置: {self.trading_unit.pm_config}")
        print(f"  商品: {symbol} / {sub_symbol}")

    def run(self) -> None:
        """執行交易主循環"""
        if not self.symbol or not self.sub_symbol:
            print("❌ 請先呼叫 configure() 設定交易商品")
            return

        print(f"🚀 啟動 TradingEngine: {self.trading_unit.name}")

        # 檢查是否有未平倉的持倉記錄 → 恢復到 PositionManager
        self._try_restore_position()

        # 訂閱商品
        self.market_service.subscribe_symbol(self.symbol, self.sub_symbol, init_days=14)

        # kbar 快取就緒後，用歷史 K 棒校正 highest/lowest price 及移停狀態
        self._reconcile_position_from_history()

        # 發送啟動通知
        self._send_startup_notification()

        # 主循環
        print_flag = False
        while True:
            try:
                current_time = datetime.now()

                # 取得即時報價
                quote = self.market_service.get_realtime_quote(
                    self.symbol, self.sub_symbol
                )
                if not quote:
                    raise Exception("無法取得即時報價")

                current_price = quote.price

                if self.position_manager.has_position:
                    # === 有倉位：高頻監控 ===

                    # 0. 時間強制平倉（日內策略用，如 ORB 13:30 收盤）
                    actions = self.position_manager.check_time_exit(
                        current_time, current_price
                    )

                    if not actions:
                        kbar_list = self.market_service.get_futures_kbars_with_timeframe(
                            self.symbol,
                            self.sub_symbol,
                            self.trading_unit.pm_config.timeframe,
                            days=5,
                        )

                        # 讓 PM 處理價格更新
                        actions = self.position_manager.on_price_update(
                            current_price, kbar_list
                        )

                    # 執行 PM 產生的指令
                    for action in actions:
                        self._execute_action(action)

                    # 加碼信號檢測（每 signal_check_interval 分鐘）
                    if (
                        self.position_manager.config.enable_addon
                        and self.position_manager.has_position
                        and current_time.minute % self.signal_check_interval == 0
                        and not self._addon_checked_this_interval
                    ):
                        self._addon_checked_this_interval = True
                        signal = self.trading_unit.strategy.evaluate(
                            kbar_list, current_price, self.sub_symbol
                        )
                        addon_actions = self.position_manager.on_signal(
                            signal, kbar_list, self.symbol, self.sub_symbol
                        )
                        for action in addon_actions:
                            self._execute_action(action)
                    elif current_time.minute % self.signal_check_interval != 0:
                        self._addon_checked_this_interval = False

                    # 同步倉位狀態到 position.json
                    self._sync_position_record(current_price)

                    # 日誌（每 5 分鐘一次）
                    if current_time.minute % 5 == 0 and not print_flag:
                        print_flag = True
                        print(
                            f"[{current_time.strftime('%H:%M:%S')}] "
                            f"價格: {current_price:.1f}"
                        )
                    elif current_time.minute % 5 != 0:
                        print_flag = False

                    wait_seconds(self.position_check_interval)

                else:
                    # === 無倉位：低頻檢測信號 ===
                    _t0 = _time.monotonic()

                    print(
                        f"\n[{current_time.strftime('%H:%M:%S')}] "
                        f"價格: {current_price:.1f}"
                    )

                    kbar_list = self.market_service.get_futures_kbars_with_timeframe(
                        self.symbol,
                        self.sub_symbol,
                        self.trading_unit.pm_config.timeframe,
                        days=5,
                    )
                    _t1 = _time.monotonic()

                    # 策略評估
                    signal = self.trading_unit.strategy.evaluate(
                        kbar_list, current_price, self.sub_symbol
                    )
                    _t2 = _time.monotonic()

                    # PM 處理信號
                    actions = self.position_manager.on_signal(
                        signal, kbar_list, self.symbol, self.sub_symbol
                    )
                    _t3 = _time.monotonic()

                    if actions:
                        print(
                            f"⏱️ kbar={(_t1-_t0)*1000:.0f}ms "
                            f"eval={(_t2-_t1)*1000:.0f}ms "
                            f"signal={(_t3-_t2)*1000:.0f}ms "
                            f"total={(_t3-_t0)*1000:.0f}ms"
                        )

                    # 執行開倉指令
                    for action in actions:
                        fill_result = self._execute_action(action)
                        if fill_result and action.order_type == "Open" and self.position_manager.position:
                                pos = self.position_manager.position
                                signal_price = pos.entry_price
                                pos.entry_price = fill_result
                                pos.highest_price = fill_result
                                pos.lowest_price = fill_result
                                for leg in pos.legs:
                                    if leg.entry_price == signal_price:
                                        leg.entry_price = fill_result

                    if not actions:
                        print("無交易訊號")

                    self._sync_position_record(current_price)

                    if not self.position_manager.has_position:
                        self._wait_with_instant_check()

            except KeyboardInterrupt:
                print("\n程式被使用者中斷")
                break
            except Exception as e:
                print(f"執行錯誤: {str(e)}")
                print("結束程式")
                break

    # ── Instant trigger monitoring ─────────────────────────

    _INSTANT_PROXIMITY = 30   # points: switch to 1s polling
    _INSTANT_POLL_FAST = 1    # seconds
    _INSTANT_POLL_NORMAL = 3  # seconds

    def _wait_with_instant_check(self) -> None:
        """Wait for next bar, but actively monitor tick prices for instant triggers.

        Adaptive polling: 3s normally, 1s when price is within proximity of a trigger.
        If a trigger is crossed, return immediately so the main loop re-evaluates.
        """
        from datetime import timedelta

        triggers = self.trading_unit.strategy.get_instant_trigger_prices()

        if not triggers:
            calculate_and_wait_to_next_execution(self.signal_check_interval, True)
            return

        now = datetime.now()
        current_minute = now.minute
        interval = self.signal_check_interval
        next_min = ((current_minute // interval) + 1) * interval
        if next_min >= 60:
            next_time = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_time = now.replace(minute=next_min, second=0, microsecond=0)

        print(f"下次執行時間: {next_time.strftime('%H:%M:%S')}")
        above_triggers = sorted(p for p, d in triggers if d == "above")
        below_triggers = sorted((p for p, d in triggers if d == "below"), reverse=True)
        print(
            f"⚡ Instant monitoring: "
            f"long triggers={[f'{p:.0f}' for p in above_triggers[:3]]}, "
            f"short triggers={[f'{p:.0f}' for p in below_triggers[:3]]}"
        )

        while datetime.now() < next_time:
            try:
                quote = self.market_service.get_realtime_quote(
                    self.symbol, self.sub_symbol
                )
                if not quote:
                    _time.sleep(self._INSTANT_POLL_NORMAL)
                    continue

                price = quote.price

                crossed = any(
                    (d == "above" and price >= p) or (d == "below" and price <= p)
                    for p, d in triggers
                )
                if crossed:
                    print(f"⚡ Instant trigger hit! price={price:.0f}")
                    return

                min_dist = min(abs(price - p) for p, _ in triggers)
                poll = (
                    self._INSTANT_POLL_FAST
                    if min_dist <= self._INSTANT_PROXIMITY
                    else self._INSTANT_POLL_NORMAL
                )
                _time.sleep(poll)

            except Exception:
                _time.sleep(self._INSTANT_POLL_NORMAL)

    def _execute_action(self, action: OrderAction) -> int | None:
        """執行下單指令並處理成交

        Returns:
            成交價格，失敗則返回 None
        """
        fill_result = self.executor.execute(action)

        if fill_result.success and fill_result.fill_price is not None:
            print(
                f"{'📈' if action.order_type == 'Open' else '📉'} "
                f"{action.action.value} x{action.quantity} @ {fill_result.fill_price} "
                f"({action.reason})"
            )

            # 開倉 → 保存持倉記錄
            if action.order_type == "Open":
                self._record_open(action, fill_result.fill_price)

            # 平倉 → 先取 entry_price（on_fill 後 position 可能變 None）
            close_entry_price = 0
            close_remaining_qty = 0
            if action.order_type == "Close" and self.position_manager.position:
                pos = self.position_manager.position
                close_leg_ids = []
                if action.leg_id:
                    close_leg_ids = [action.leg_id]
                elif "leg_ids" in action.metadata:
                    close_leg_ids = action.metadata["leg_ids"]
                if close_leg_ids:
                    leg_eps = [
                        leg.entry_price for leg in pos.legs
                        if leg.leg_id in close_leg_ids and leg.entry_price
                    ]
                    if leg_eps:
                        close_entry_price = sum(leg_eps) // len(leg_eps)
                if not close_entry_price:
                    close_entry_price = pos.entry_price
                close_remaining_qty = pos.open_quantity - action.quantity

            # 平倉 → 通知 PM 並更新 Google Sheets per-leg
            if action.order_type == "Close" and action.leg_id:
                exit_reason_str = action.metadata.get("exit_reason", "SL")
                exit_reason = ExitReason(exit_reason_str)
                self.position_manager.on_fill(
                    leg_id=action.leg_id,
                    fill_price=fill_result.fill_price,
                    fill_time=fill_result.fill_time or datetime.now(),
                    exit_reason=exit_reason,
                )
                self._record_leg_close(
                    action, fill_result.fill_price, exit_reason,
                    leg_ids=[action.leg_id],
                )
            elif action.order_type == "Close" and "leg_ids" in action.metadata:
                exit_reason_str = action.metadata.get("exit_reason", "FS")
                exit_reason = ExitReason(exit_reason_str)
                for leg_id in action.metadata["leg_ids"]:
                    self.position_manager.on_fill(
                        leg_id=leg_id,
                        fill_price=fill_result.fill_price,
                        fill_time=fill_result.fill_time or datetime.now(),
                        exit_reason=exit_reason,
                    )
                self._record_leg_close(
                    action, fill_result.fill_price, exit_reason,
                    leg_ids=action.metadata["leg_ids"],
                )

            # 發送通知
            if self.line_bot_service:
                try:
                    strategy = self.record_service.strategy_name if self.record_service else ""
                    pm = self.position_manager
                    if action.order_type == "Open":
                        sl_price = 0
                        total_qty = 0
                        if pm.position and pm.position.open_legs:
                            sl_price = pm.position.open_legs[0].exit_rule.stop_loss_price or 0
                            total_qty = pm.position.open_quantity
                        self.line_bot_service.send_open_position_message(
                            symbol=action.symbol,
                            sub_symbol=action.sub_symbol,
                            price=fill_result.fill_price,
                            quantity=action.quantity,
                            action=action.action.value,
                            stop_loss_price=sl_price,
                            strategy_name=strategy,
                            reason=action.reason,
                            total_quantity=total_qty,
                        )
                    elif action.order_type == "Close":
                        equity = None
                        if self.account_service:
                            try:
                                margin = self.account_service.get_margin()
                                equity = margin.equity
                            except Exception:
                                pass
                        self.line_bot_service.send_close_position_message(
                            symbol=action.symbol,
                            sub_symbol=action.sub_symbol,
                            price=fill_result.fill_price,
                            quantity=action.quantity,
                            exit_reason=action.reason,
                            entry_price=close_entry_price,
                            strategy_name=strategy,
                            remaining_quantity=max(close_remaining_qty, 0),
                            equity=equity,
                        )
                except Exception as e:
                    print(f"發送通知失敗: {e}")

            return fill_result.fill_price
        else:
            print(f"❌ 下單失敗: {fill_result.message}")
            return None

    def _sync_position_record(self, current_price: float) -> None:
        """同步倉位狀態 + 即時價格到 position.json（供 dashboard 讀取）"""
        if not self.record_service or not self.sub_symbol:
            return

        pm = self.position_manager
        pos = pm.position

        try:
            records = self.record_service._load_records(self.record_service.record_file)

            # Always write live metadata
            records["_live"] = {
                "current_price": int(current_price),
                "timestamp": datetime.now().isoformat(),
                "strategy": self.record_service.strategy_name,
                "symbol": self.symbol,
                "sub_symbol": self.sub_symbol,
                "config_file": self.config_file,
            }

            pending = self.trading_unit.strategy.get_pending_state()
            if pending:
                records["_live"]["strategy_state"] = pending

            if pos and self.sub_symbol in records:
                is_long = pos.direction.value == "Buy"
                ts_prices = [
                    leg.exit_rule.trailing_stop_price
                    for leg in pos.open_legs
                    if leg.exit_rule.trailing_stop_active
                    and leg.exit_rule.trailing_stop_price
                ]
                rec = records[self.sub_symbol]
                rec["highest_price"] = pos.highest_price
                rec["trailing_stop_active"] = any(
                    leg.exit_rule.trailing_stop_active for leg in pos.open_legs
                )
                rec["stop_loss_price"] = (
                    pos.open_legs[0].exit_rule.stop_loss_price or 0
                    if pos.open_legs
                    else 0
                )
                rec["start_trailing_stop_price"] = (
                    pos.open_legs[0].exit_rule.start_trailing_stop_price
                    if pos.open_legs
                    else None
                )
                rec["trailing_stop_price"] = (
                    (max(ts_prices) if is_long else min(ts_prices))
                    if ts_prices
                    else None
                )
                rec["quantity"] = sum(leg.quantity for leg in pos.open_legs)

                # ORB metadata: key_levels, TP, etc.
                if pos.metadata:
                    meta_keys = [
                        "key_levels", "next_key_level_idx", "key_level_buffer",
                        "or_high", "or_low", "or_mid", "or_range",
                        "override_take_profit_points",
                        "override_start_trailing_stop_points",
                        "override_trailing_stop_points",
                    ]
                    pos_meta = {}
                    for k in meta_keys:
                        if k in pos.metadata:
                            pos_meta[k] = pos.metadata[k]
                    if pos_meta:
                        rec["position_metadata"] = pos_meta

            self.record_service.record_file.write_text(
                json.dumps(records, indent=2, ensure_ascii=False)
            )
        except Exception:
            pass

    def _try_restore_position(self) -> None:
        """啟動時檢查 position.json，如有未平倉記錄則恢復到 PositionManager"""
        if not self.sub_symbol:
            return

        record = self.record_service.get_position(self.sub_symbol)
        if record is None:
            print("📋 無未平倉記錄，正常啟動")
            return

        print(f"📋 發現未平倉記錄: {record.sub_symbol} @ {record.entry_price}")
        self.position_manager.restore_position(record)

    def _reconcile_position_from_history(self) -> None:
        """用歷史 K 棒校正 highest/lowest price，補回停機期間的極值。

        在 subscribe_symbol 之後呼叫（kbar 快取已初始化）。
        掃描入場時間之後的所有 K 棒，找出真正的最高/最低價，
        並重新觸發移停啟動邏輯，確保重啟後移停狀態正確。
        """
        pos = self.position_manager.position
        if pos is None:
            return

        kbar_list = self.market_service.get_futures_kbars_with_timeframe(
            self.symbol,
            self.sub_symbol,
            self.trading_unit.pm_config.timeframe,
            days=5,
        )
        if not kbar_list or len(kbar_list) == 0:
            return

        entry_time = pos.entry_time
        is_long = pos.direction == Action.Buy
        old_highest = pos.highest_price
        old_lowest = pos.lowest_price

        hist_high = old_highest
        hist_low = old_lowest

        for kb in kbar_list.kbars:
            if kb.time < entry_time:
                continue
            if kb.high > hist_high:
                hist_high = int(kb.high)
            if kb.low < hist_low:
                hist_low = int(kb.low)

        if hist_high == old_highest and hist_low == old_lowest:
            print("📋 歷史校正: highest/lowest 與記錄一致，無需更新")
            return

        pos.highest_price = hist_high
        pos.lowest_price = hist_low

        extreme = hist_high if is_long else hist_low
        old_extreme = old_highest if is_long else old_lowest
        print(
            f"📋 歷史校正: {'最高' if is_long else '最低'}價 "
            f"{old_extreme} → {extreme}"
        )

        # 用校正後的極值重新觸發移停邏輯
        self.position_manager._update_trailing_stops(extreme)

    def _record_open(self, action: OrderAction, fill_price: int) -> None:
        """開倉時保存持倉記錄到 position.json + Google Sheets（每個 leg 一行）"""
        try:
            pm = self.position_manager
            position = pm.position
            if not position:
                return

            sl_price = None
            if position.open_legs:
                sl_price = position.open_legs[0].exit_rule.stop_loss_price

            # 讀取既有的 position record（加碼時保留原始資料）
            existing_record = self.record_service.get_position(action.sub_symbol)
            existing_row_map = {}
            existing_legs_info = {}
            if existing_record:
                if existing_record.sheets_row_map:
                    existing_row_map = dict(existing_record.sheets_row_map)
                if existing_record.legs_info:
                    existing_legs_info = dict(existing_record.legs_info)

            # 新倉位：用實際成交價更新 entry_price 及所有出場參數
            if not existing_record:
                pm.update_entry_on_fill(fill_price)

            record = PositionRecord(
                symbol=action.symbol,
                sub_symbol=action.sub_symbol,
                direction=action.action,
                entry_time=existing_record.entry_time if existing_record else datetime.now(),
                timeframe=pm.config.timeframe,
                quantity=position.open_quantity,
                entry_price=position.entry_price,
                stop_loss_price=sl_price,
                start_trailing_stop_price=(
                    position.open_legs[0].exit_rule.start_trailing_stop_price
                    if position.open_legs
                    else None
                ),
                take_profit_price=(
                    position.open_legs[0].exit_rule.take_profit_price
                    if position.open_legs
                    else None
                ),
                trailing_stop_active=(
                    position.open_legs[0].exit_rule.trailing_stop_active
                    if position.open_legs
                    else False
                ),
                highest_price=position.highest_price or fill_price,
                sheets_row_map=existing_row_map,
                legs_info=existing_legs_info,
            )

            # 找出尚未記錄的新 legs
            new_legs = []
            for leg in position.open_legs:
                is_new = leg.leg_id not in existing_row_map
                leg_ep = leg.entry_price or fill_price
                if is_new:
                    new_legs.append({
                        "leg_id": leg.leg_id,
                        "quantity": leg.quantity,
                        "entry_price": leg_ep,
                    })
                if leg.leg_id not in existing_legs_info:
                    if record.legs_info is None:
                        record.legs_info = {}
                    record.legs_info[leg.leg_id] = {
                        "entry_price": leg_ep,
                        "quantity": leg.quantity,
                        "leg_type": leg.leg_type.value,
                    }

            # 為新 legs 建立 Google Sheets 記錄
            if new_legs:
                new_row_map = self.record_service.log_legs_open(record, new_legs)
                if record.sheets_row_map is None:
                    record.sheets_row_map = {}
                record.sheets_row_map.update(new_row_map)

            self.record_service.save_position(record)
        except Exception as e:
            print(f"⚠️ 保存開倉記錄失敗: {e}")

    def _record_leg_close(
        self,
        action: OrderAction,
        fill_price: int,
        exit_reason: ExitReason,
        leg_ids: list[str],
    ) -> None:
        """平倉時更新 Google Sheets 對應 leg 的記錄，並在全部平倉後清除 position.json"""
        try:
            pm = self.position_manager
            strategy_params = {
                "stop_loss_points": pm.config.stop_loss_points,
                "start_trailing_stop_points": pm.config.start_trailing_stop_points,
                "trailing_stop_points": pm.config.trailing_stop_points,
                "take_profit_points": pm.config.take_profit_points,
            }

            # 取得 sheets_row_map
            existing_record = self.record_service.get_position(action.sub_symbol)
            row_map = {}
            if existing_record and existing_record.sheets_row_map:
                row_map = existing_record.sheets_row_map

            # 更新每個被平倉 leg 的 Google Sheets 記錄
            for leg_id in leg_ids:
                row_number = row_map.get(leg_id)
                if row_number:
                    self.record_service.log_leg_close(
                        leg_id=leg_id,
                        row_number=row_number,
                        exit_price=float(fill_price),
                        exit_reason=exit_reason,
                        strategy_params=strategy_params,
                    )

            # 全部平倉 → 清除 position.json
            if pm.position is None:
                self.record_service.remove_position(sub_symbol=action.sub_symbol)
            else:
                # 部分平倉 → 更新 position.json（移除已平倉 leg 的條目）
                legs_info = existing_record.legs_info or {}
                for leg_id in leg_ids:
                    row_map.pop(leg_id, None)
                    legs_info.pop(leg_id, None)
                existing_record.sheets_row_map = row_map
                existing_record.legs_info = legs_info
                existing_record.quantity = pm.position.open_quantity
                self.record_service.save_position(existing_record)

        except Exception as e:
            print(f"⚠️ 記錄平倉失敗: {e}")

    def _send_startup_notification(self) -> None:
        """發送系統啟動通知"""
        if not self.line_bot_service or not self.account_service:
            return

        try:
            quote = self.market_service.get_realtime_quote(self.symbol, self.sub_symbol)
            current_price = quote.price if quote else "N/A"
            margin = self.account_service.get_margin()

            strategy = self.record_service.strategy_name if self.record_service else "unknown"
            pos_qty = (
                self.position_manager.position.open_quantity
                if self.position_manager.has_position
                else 0
            )
            self.line_bot_service.send_status_message(
                total_equity=margin.equity_amount,
                contract=self.sub_symbol,
                price=current_price,
                position=pos_qty,
                status=f"策略 [{strategy}] 已啟動",
            )
        except Exception as e:
            print(f"發送啟動通知失敗: {e}")
