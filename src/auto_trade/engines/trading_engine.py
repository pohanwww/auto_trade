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
from datetime import datetime

from auto_trade.executors.base_executor import BaseExecutor
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

    def configure(
        self,
        symbol: str,
        sub_symbol: str,
        signal_check_interval: int = 5,
        position_check_interval: int = 5,
    ) -> None:
        """設定交易參數

        Args:
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            signal_check_interval: 信號檢測間隔（分鐘）
            position_check_interval: 持倉檢測間隔（秒）
        """
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
        self.market_service.subscribe_symbol(self.symbol, self.sub_symbol, init_days=30)

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
                    kbar_list = self.market_service.get_futures_kbars_with_timeframe(
                        self.symbol,
                        self.sub_symbol,
                        self.trading_unit.pm_config.timeframe,
                        days=15,
                    )

                    # 讓 PM 處理價格更新
                    actions = self.position_manager.on_price_update(
                        current_price, kbar_list
                    )

                    # 執行 PM 產生的指令
                    for action in actions:
                        self._execute_action(action)

                    # 持倉狀態有變動時同步到 position.json
                    self._sync_position_record()

                    # 更新 dashboard status
                    self._write_dashboard_status(current_price)

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
                    print(
                        f"\n[{current_time.strftime('%H:%M:%S')}] "
                        f"價格: {current_price:.1f}"
                    )

                    kbar_list = self.market_service.get_futures_kbars_with_timeframe(
                        self.symbol,
                        self.sub_symbol,
                        self.trading_unit.pm_config.timeframe,
                        days=15,
                    )

                    # 策略評估
                    signal = self.trading_unit.strategy.evaluate(
                        kbar_list, current_price, self.sub_symbol
                    )

                    # PM 處理信號
                    actions = self.position_manager.on_signal(
                        signal, kbar_list, self.symbol, self.sub_symbol
                    )

                    # 執行開倉指令
                    for action in actions:
                        fill_result = self._execute_action(action)
                        if fill_result and action.order_type == "Open" and self.position_manager.position:
                                self.position_manager.position.entry_price = fill_result
                                self.position_manager.position.highest_price = (
                                    fill_result
                                )
                                self.position_manager.position.lowest_price = (
                                    fill_result
                                )

                    if not actions:
                        print("無交易訊號")

                    self._write_dashboard_status(current_price)

                    calculate_and_wait_to_next_execution(
                        self.signal_check_interval, True
                    )

            except KeyboardInterrupt:
                print("\n程式被使用者中斷")
                break
            except Exception as e:
                print(f"執行錯誤: {str(e)}")
                print("結束程式")
                break

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

            # 平倉 → 通知 PM 並更新記錄
            if action.order_type == "Close" and action.leg_id:
                exit_reason_str = action.metadata.get("exit_reason", "SL")
                exit_reason = ExitReason(exit_reason_str)
                self.position_manager.on_fill(
                    leg_id=action.leg_id,
                    fill_price=fill_result.fill_price,
                    fill_time=fill_result.fill_time or datetime.now(),
                    exit_reason=exit_reason,
                )
                self._check_and_record_close(
                    action, fill_result.fill_price, exit_reason
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
                self._check_and_record_close(
                    action, fill_result.fill_price, exit_reason
                )

            # 發送通知
            if self.line_bot_service:
                try:
                    if action.order_type == "Open":
                        self.line_bot_service.send_open_position_message(
                            symbol=action.symbol,
                            sub_symbol=action.sub_symbol,
                            price=fill_result.fill_price,
                            quantity=action.quantity,
                            action=action.action,
                            stop_loss_price=0,  # TODO: 從 PM 取得
                        )
                except Exception as e:
                    print(f"發送通知失敗: {e}")

            return fill_result.fill_price
        else:
            print(f"❌ 下單失敗: {fill_result.message}")
            return None

    _last_synced_highest: int = 0
    _last_synced_ts_active: bool = False
    _last_synced_sl: int = 0

    def _sync_position_record(self) -> None:
        """持倉狀態有變動時同步到 position.json"""
        pm = self.position_manager
        if not pm.position or not self.sub_symbol:
            return

        pos = pm.position
        has_active_ts = any(
            leg.exit_rule.trailing_stop_active for leg in pos.open_legs
        )
        sl_price = pos.open_legs[0].exit_rule.stop_loss_price or 0 if pos.open_legs else 0

        # 沒有變動就跳過
        if (
            pos.highest_price == self._last_synced_highest
            and has_active_ts == self._last_synced_ts_active
            and sl_price == self._last_synced_sl
        ):
            return

        try:
            import json

            records = self.record_service._load_records(self.record_service.record_file)
            if self.sub_symbol in records:
                records[self.sub_symbol]["highest_price"] = pos.highest_price
                records[self.sub_symbol]["trailing_stop_active"] = has_active_ts
                records[self.sub_symbol]["stop_loss_price"] = sl_price
                self.record_service.record_file.write_text(
                    json.dumps(records, indent=2, ensure_ascii=False)
                )

            self._last_synced_highest = pos.highest_price
            self._last_synced_ts_active = has_active_ts
            self._last_synced_sl = sl_price
        except Exception as e:
            print(f"⚠️ 同步持倉記錄失敗: {e}")

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

    def _record_open(self, action: OrderAction, fill_price: int) -> None:
        """開倉時保存持倉記錄到 position.json + Google Sheets"""
        try:
            pm = self.position_manager
            position = pm.position
            if not position:
                return

            sl_price = None
            if position.open_legs:
                sl_price = position.open_legs[0].exit_rule.stop_loss_price

            record = PositionRecord(
                symbol=action.symbol,
                sub_symbol=action.sub_symbol,
                direction=action.action,
                entry_time=datetime.now(),
                timeframe=pm.config.timeframe,
                quantity=action.quantity,
                entry_price=fill_price,
                stop_loss_price=sl_price,
                highest_price=fill_price,
            )
            self.record_service.save_position(record)
        except Exception as e:
            print(f"⚠️ 保存開倉記錄失敗: {e}")

    def _check_and_record_close(
        self,
        action: OrderAction,
        fill_price: int,
        exit_reason: ExitReason,
    ) -> None:
        """平倉後如果所有 Leg 都已關閉，移除持倉記錄"""
        pm = self.position_manager
        if pm.position is not None:
            return

        try:
            strategy_params = {
                "stop_loss_points": pm.config.stop_loss_points,
                "start_trailing_stop_points": pm.config.start_trailing_stop_points,
                "trailing_stop_points": pm.config.trailing_stop_points,
                "take_profit_points": pm.config.take_profit_points,
            }
            self.record_service.remove_position(
                sub_symbol=action.sub_symbol,
                exit_price=float(fill_price),
                exit_reason=exit_reason,
                strategy_params=strategy_params,
            )
        except Exception as e:
            print(f"⚠️ 移除平倉記錄失敗: {e}")

    def _write_dashboard_status(self, current_price: float) -> None:
        """Write status.json for the dashboard to read."""
        if not self.record_service:
            return
        status_file = self.record_service.record_file.parent / "status.json"
        pm = self.position_manager
        pos = pm.position

        data: dict = {
            "strategy": self.record_service.strategy_name,
            "symbol": self.symbol,
            "sub_symbol": self.sub_symbol,
            "current_price": int(current_price),
            "timestamp": datetime.now().isoformat(),
            "has_position": pos is not None,
        }

        if pos:
            ts_prices = [
                leg.exit_rule.trailing_stop_price
                for leg in pos.open_legs
                if leg.exit_rule.trailing_stop_active and leg.exit_rule.trailing_stop_price
            ]
            data.update({
                "direction": pos.direction.value,
                "entry_price": pos.entry_price,
                "quantity": sum(leg.quantity for leg in pos.open_legs),
                "stop_loss_price": (
                    pos.open_legs[0].exit_rule.stop_loss_price
                    if pos.open_legs
                    else None
                ),
                "trailing_stop_active": any(
                    leg.exit_rule.trailing_stop_active for leg in pos.open_legs
                ),
                "trailing_stop_price": max(ts_prices) if ts_prices else None,
                "take_profit_price": (
                    pos.open_legs[0].exit_rule.take_profit_price
                    if pos.open_legs and pos.open_legs[0].exit_rule.take_profit_price
                    else None
                ),
                "highest_price": pos.highest_price,
                "entry_time": (
                    pos.entry_time.isoformat() if pos.entry_time else None
                ),
            })

        try:
            status_file.parent.mkdir(parents=True, exist_ok=True)
            status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def _send_startup_notification(self) -> None:
        """發送系統啟動通知"""
        if not self.line_bot_service or not self.account_service:
            return

        try:
            quote = self.market_service.get_realtime_quote(self.symbol, self.sub_symbol)
            current_price = quote.price if quote else "N/A"
            margin = self.account_service.get_margin()

            self.line_bot_service.send_status_message(
                total_equity=margin.equity_amount,
                contract=self.sub_symbol,
                price=current_price,
                position=0,
            )
        except Exception as e:
            print(f"發送啟動通知失敗: {e}")
