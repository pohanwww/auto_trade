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

from datetime import datetime

from auto_trade.executors.base_executor import BaseExecutor
from auto_trade.models.position import OrderAction
from auto_trade.models.position_record import ExitReason
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
                        if fill_result and action.order_type == "Open":
                            # 更新 PM 的 position 入場價
                            if self.position_manager.position:
                                self.position_manager.position.entry_price = fill_result
                                self.position_manager.position.highest_price = (
                                    fill_result
                                )
                                self.position_manager.position.lowest_price = (
                                    fill_result
                                )

                    if not actions:
                        print("無交易訊號")

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

            # 如果是平倉，通知 PM
            if action.order_type == "Close" and action.leg_id:
                exit_reason_str = action.metadata.get("exit_reason", "SL")
                exit_reason = ExitReason(exit_reason_str)
                self.position_manager.on_fill(
                    leg_id=action.leg_id,
                    fill_price=fill_result.fill_price,
                    fill_time=fill_result.fill_time or datetime.now(),
                    exit_reason=exit_reason,
                )
            elif action.order_type == "Close" and "leg_ids" in action.metadata:
                # 批量平倉（如 MACD 快速停損）
                exit_reason_str = action.metadata.get("exit_reason", "FS")
                exit_reason = ExitReason(exit_reason_str)
                for leg_id in action.metadata["leg_ids"]:
                    self.position_manager.on_fill(
                        leg_id=leg_id,
                        fill_price=fill_result.fill_price,
                        fill_time=fill_result.fill_time or datetime.now(),
                        exit_reason=exit_reason,
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
