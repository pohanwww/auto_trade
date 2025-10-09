"""Trading service for managing automated trading operations."""

import time
from datetime import datetime, timedelta

from auto_trade.models import (
    Action,
    ExitReason,
    FuturePosition,
    FuturesTrade,
    StrategyInput,
)
from auto_trade.models.position_record import PositionRecord
from auto_trade.services.account_service import AccountService
from auto_trade.services.market_service import MarketService
from auto_trade.services.order_service import OrderService
from auto_trade.services.record_service import RecordService
from auto_trade.services.strategy_service import StrategyService
from auto_trade.utils import calculate_and_wait_to_next_execution, wait_seconds


class TradingService:
    """交易服務類別"""

    def __init__(
        self,
        api_client,
        account_service: AccountService,
        market_service: MarketService,
        order_service: OrderService,
        strategy_service: StrategyService,
    ):
        self.api_client = api_client
        self.account_service = account_service
        self.market_service = market_service
        self.order_service = order_service
        self.strategy_service = strategy_service

        # 記錄服務（自動從 Config 讀取 Google Sheets 設定）
        self.record_service = RecordService()

        # 交易狀態追蹤
        self.current_position: FuturePosition | None = None
        self.entry_price: float = 0.0
        self.trailing_stop_active: bool = False
        self.stop_loss_price: float = 0.0  # 停損價格 (共用於初始停損和移動停損)
        self.last_sync_time: datetime | None = None

        # 交易參數 (預設值)
        self.trailing_stop_points: int = 200
        self.start_trailing_stop_points: int = 200
        self.order_quantity: int = 1
        self.stop_loss_points: int = 50
        self.take_profit_points: int = 500
        self.timeframe: str = "30m"  # K線時間尺度

        # 檢測頻率參數
        self.signal_check_interval: int = 5  # 訊號檢測間隔 (分鐘)
        self.position_check_interval: int = 5  # 持倉檢測間隔 (秒)

    def set_trading_params(self, params: dict):
        """設定交易參數"""
        self.trailing_stop_points = params.get("trailing_stop_points", 200)
        self.start_trailing_stop_points = params.get("start_trailing_stop_points", 200)
        self.order_quantity = params.get("order_quantity", 1)
        self.stop_loss_points = params.get("stop_loss_points", 50)
        self.take_profit_points = params.get("take_profit_points", 500)
        self.timeframe = params.get("timeframe", "30m")

        # 檢測頻率參數
        self.signal_check_interval = params.get("signal_check_interval", 5)
        self.position_check_interval = params.get("position_check_interval", 5)

        print("交易參數已設定:")
        print(f"  移動停損點數: {self.trailing_stop_points}")
        print(f"  啟動移動停損點數: {self.start_trailing_stop_points}")
        print(f"  下單數量: {self.order_quantity}")
        print(f"  初始停損點數: {self.stop_loss_points}")
        print(f"  獲利了結點數: {self.take_profit_points}")
        print(f"  K線時間尺度: {self.timeframe}")
        print(f"  訊號檢測間隔: {self.signal_check_interval} 分鐘")
        print(f"  持倉檢測間隔: {self.position_check_interval} 秒")

    def _convert_sub_symbol_to_contract_code(self, sub_symbol: str) -> str:
        """將 sub_symbol (如 MXF202510) 轉換為合約代碼 (如 MXFJ5)

        Shioaji 期貨月份代碼映射（與標準略有不同）：
        A=1月, B=2月, C=3月, D=4月, E=5月, F=6月,
        G=7月, H=8月, I=9月, J=10月, K=11月, L=12月
        """
        if len(sub_symbol) < 8:
            return sub_symbol
        commodity = sub_symbol[:3]
        year = sub_symbol[3:7]
        month = sub_symbol[7:9]
        # Shioaji 使用的月份代碼（與國際標準不同）
        month_codes = {
            "01": "A",
            "02": "B",
            "03": "C",
            "04": "D",
            "05": "E",
            "06": "F",
            "07": "G",
            "08": "H",
            "09": "I",
            "10": "J",
            "11": "K",
            "12": "L",
        }
        year_code = year[-1]
        month_code = month_codes.get(month, month)
        contract_code = f"{commodity}{month_code}{year_code}"
        return contract_code

    def _get_latest_trade(self, trades: list[FuturesTrade]) -> FuturesTrade | None:
        """根據成交時間獲取最新的交易記錄

        Args:
            trades: 交易記錄列表

        Returns:
            最新的交易記錄，如果沒有則返回 None
        """
        if not trades:
            return None

        # 過濾出有成交記錄的交易
        trades_with_deals = [trade for trade in trades if trade.status.deals]
        if not trades_with_deals:
            return None

        # 找到最新的成交時間
        latest_trade = None
        latest_time = None

        for trade in trades_with_deals:
            # 取該交易的最後一筆成交時間
            last_deal_time = trade.status.deals[-1].time
            if latest_time is None or last_deal_time > latest_time:
                latest_time = last_deal_time
                latest_trade = trade

        return latest_trade

    def _calculate_trailing_stop_from_history(
        self, symbol: str, sub_symbol: str, entry_time: datetime, entry_price: float
    ) -> tuple[float, bool]:
        """根據進場時間計算當前應有的移動停損狀態

        Args:
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            entry_time: 進場時間
            entry_price: 進場價格

        Returns:
            tuple[stop_loss_price, trailing_stop_active]: 停損價格和移動停損狀態

        Raises:
            ValueError: 當無法計算停損價格時
        """
        # 計算需要多少天的數據
        now = datetime.now()
        days_diff = max((now - entry_time).days + 1, 30)
        print(f"計算移動停損: 從 {entry_time} 到現在，需要 {days_diff} 天數據")

        # 獲取歷史K棒數據
        kbars = self.market_service.get_futures_historical_kbars(
            symbol, sub_symbol, days_diff
        )

        if not kbars or len(kbars.kbars) < 30:
            raise ValueError(
                f"歷史數據不足: 需要至少 30 根K棒，實際獲得 {len(kbars.kbars) if kbars else 0} 根"
            )

        # 轉換為30分鐘K棒
        kbars_30m = self.market_service.resample_kbars(kbars, "30m")

        # 計算初始停損（進場前30根K棒最低點）
        pre_entry_kbars = [kbar for kbar in kbars_30m.kbars if kbar.time <= entry_time]
        if len(pre_entry_kbars) >= 30:
            min_price = min(kbar.low for kbar in pre_entry_kbars[-30:])
            initial_stop_loss = min_price - self.stop_loss_points
            print(
                f"初始停損計算: 前30根最低點 {min_price:.1f} - {self.stop_loss_points} = {initial_stop_loss:.1f}"
            )
        else:
            raise ValueError(
                f"進場前K棒數據不足: 需要至少 30 根，實際獲得 {len(pre_entry_kbars)} 根"
            )

        # 找到進場後的K棒
        post_entry_kbars = [kbar for kbar in kbars_30m.kbars if kbar.time >= entry_time]

        if not post_entry_kbars:
            print(f"進場後無K棒數據，使用初始停損: {initial_stop_loss:.1f}")
            return initial_stop_loss, False

        # 計算進場後最高價格（只支持做多）
        highest_price = max(kbar.high for kbar in post_entry_kbars)
        profit_points = highest_price - entry_price
        print(f"進場後最高價: {highest_price:.1f}, 最高獲利: {profit_points:.1f} 點")

        # 檢查是否應該啟動移動停損
        if profit_points >= self.start_trailing_stop_points:
            trailing_stop_loss = highest_price - self.trailing_stop_points
            print(f"✅ 移動停損已啟動，停損價格: {trailing_stop_loss:.1f}")
            return trailing_stop_loss, True
        else:
            print(f"移動停損未啟動，使用初始停損: {initial_stop_loss:.1f}")
            return initial_stop_loss, False

    def _initialize_existing_position(self, symbol: str, sub_symbol: str):
        """初始化現有持倉的停損信息"""
        try:
            print("初始化現有持倉的停損信息...")

            # 優先從本地記錄讀取持倉信息
            local_record = self.record_service.get_position(sub_symbol)
            if local_record:
                print("✅ 從本地記錄還原持倉信息")
                print(f"進場時間: {local_record.entry_time}")
                print(f"進場價格: {local_record.entry_price:.1f}")

                # 還原進場價格
                self.entry_price = local_record.entry_price

                # 使用 entry_time 重新計算移動停損狀態
                calculated_stop_loss, self.trailing_stop_active = (
                    self._calculate_trailing_stop_from_history(
                        symbol,
                        sub_symbol,
                        local_record.entry_time,
                        local_record.entry_price,
                    )
                )

                if self.trailing_stop_active:
                    self.stop_loss_price = calculated_stop_loss
                elif local_record.stop_loss_price:
                    self.stop_loss_price = local_record.stop_loss_price
                elif calculated_stop_loss:
                    self.stop_loss_price = calculated_stop_loss
                else:
                    raise ValueError(
                        f"無法確定停損價格: trailing_stop_active={self.trailing_stop_active}, "
                        f"local_record.stop_loss_price={local_record.stop_loss_price}, "
                        f"calculated_stop_loss={calculated_stop_loss}"
                    )

                print(f"獲利了結價格: {self.entry_price + self.take_profit_points:.1f}")
                print("現有持倉初始化完成 (使用本地記錄)")
                self.record_service.update_stop_loss(
                    sub_symbol,
                    self.stop_loss_price,
                    self.trailing_stop_active,
                )
                return

            # 如果本地記錄不存在，使用備用方案
            print("⚠️  本地記錄不存在，使用備用方案")
            print(f"進場價格: {self.current_position.price:.1f}")
            self.entry_price = self.current_position.price

            # 初始化 open_time 為 None
            open_time = None

            # 獲取開倉時間 - 從交易記錄中查找
            try:
                print(f"查詢交易記錄: symbol={symbol}, sub_symbol={sub_symbol}")

                # 使用轉換後的合約代碼查詢
                contract_code = self._convert_sub_symbol_to_contract_code(sub_symbol)
                print(f"轉換後的合約代碼: {contract_code}")

                trades = self.order_service.check_order_status(
                    symbol=symbol, sub_symbol=contract_code
                )

                print(f"找到 {len(trades)} 筆交易記錄")
                filled_trades = [
                    t
                    for t in trades
                    if t.status.status in ["Filled", "PartFilled", "Status.Filled"]
                ]
                print(f"找到 {len(filled_trades)} 筆已成交交易")

                if filled_trades:
                    # 根據成交時間取最新的交易記錄
                    latest_trade = self._get_latest_trade(filled_trades)
                    if latest_trade and latest_trade.status.deals:
                        # 取最後一筆成交的時間
                        last_deal = latest_trade.status.deals[-1]
                        open_time = last_deal.time
                        print(
                            f"✅ 從交易記錄獲取開倉時間: {open_time} (成交時間: {last_deal.time})"
                        )

                        # 使用統一函數計算移動停損
                        self.stop_loss_price, self.trailing_stop_active = (
                            self._calculate_trailing_stop_from_history(
                                symbol, sub_symbol, open_time, self.entry_price
                            )
                        )
                    else:
                        # 沒有成交記錄，使用持倉價格
                        self.stop_loss_price = self.current_position.price - 50
                        print(
                            f"沒有成交記錄，使用持倉價格計算停損: {self.stop_loss_price:.1f}"
                        )
                else:
                    # 沒有找到成交記錄，使用持倉價格
                    self.stop_loss_price = self.current_position.price - 50
                    print(
                        f"沒有找到成交記錄，使用持倉價格計算停損: {self.stop_loss_price:.1f}"
                    )

            except Exception as e:
                print(f"計算基於開倉時間的停損失敗: {e}")
                # 備用方案：使用持倉價格
                self.stop_loss_price = self.current_position.price - 50
                print(f"使用備用方案計算停損: {self.stop_loss_price:.1f}")

            # 計算獲利了結價格（只支持做多）
            self.take_profit_points = self.entry_price + self.take_profit_points

            print(f"獲利了結價格: {self.take_profit_points:.1f}")
            print(f"移動停損觸發點數: {self.start_trailing_stop_points}")

            position_record = PositionRecord(
                symbol=symbol,
                sub_symbol=sub_symbol,
                direction=self.current_position.direction,
                quantity=self.current_position.quantity,
                entry_price=self.entry_price,
                entry_time=open_time
                if open_time is not None
                else datetime.now().replace(microsecond=0),
                stop_loss_price=self.stop_loss_price,
                timeframe=self.timeframe,
                trailing_stop_active=False,
            )
            self.record_service.save_position(position_record)
            print("備用方案的持倉信息已保存到本地記錄")

            print("現有持倉初始化完成 (使用備用方案)")

        except Exception as e:
            print(f"初始化現有持倉失敗: {e}")

    def _get_current_position(self, sub_symbol: str) -> FuturePosition | None:
        """取得當前持倉"""
        try:
            positions = self.account_service.get_future_positions()
            contract_code = self._convert_sub_symbol_to_contract_code(sub_symbol)
            print(f"查找持倉: sub_symbol={sub_symbol} → contract_code={contract_code}")

            for pos in positions:
                print(f"檢查持倉: code={pos.code}, quantity={pos.quantity}")
                if pos.code == contract_code and pos.quantity != 0:
                    # 設定 sub_symbol 以便後續使用
                    pos.sub_symbol = sub_symbol
                    print(f"找到持倉: {pos}")
                    return pos
            return None
        except Exception as e:
            print(f"取得持倉失敗: {str(e)}")
            return None

    def _check_close_position_trigger(
        self, symbol: str, sub_symbol: str, current_price: float
    ) -> bool:
        """
        檢查是否觸發了停損或獲利了結，如果觸發則立即下市價單平倉

        Returns:
            bool: 是否觸發了停損或獲利了結
        """
        if not self.current_position or self.stop_loss_price == 0.0:
            return False

        # 計算當前獲利點數（只支持做多）
        current_profit = current_price - self.entry_price
        stop_triggered = current_price <= self.stop_loss_price
        profit_triggered = current_profit >= self.take_profit_points
        # 檢查是否觸發停損或獲利了結
        if stop_triggered or profit_triggered:
            # 平倉（賣出）
            fill_price = self._place_market_order_and_wait(
                symbol, sub_symbol, Action.Sell, "Close"
            )

            if fill_price is not None:
                # 確定出場原因
                if profit_triggered:
                    exit_reason = ExitReason.TAKE_PROFIT
                    print(
                        f"獲利了結觸發! 當前獲利: {current_profit:.1f} 點 >= {self.take_profit_points} 點, 成交價格: {fill_price}"
                    )
                elif stop_triggered:
                    if self.trailing_stop_active:
                        exit_reason = ExitReason.TRAILING_STOP
                    else:
                        exit_reason = ExitReason.STOP_LOSS
                    print(
                        f"停損觸發! 當前價格: {current_price}, 停損價格: {self.stop_loss_price}, 成交價格: {fill_price}"
                    )
                else:
                    exit_reason = ExitReason.OTHER

                # 移除本地持倉記錄並記錄平倉資訊
                self.record_service.remove_position(sub_symbol, fill_price, exit_reason)

                # 重置狀態
                self.current_position = None
                self.trailing_stop_active = False
                self.stop_loss_price = 0.0
                self.entry_price = 0.0
            return True
        return False

    def _update_trailing_stop(self, current_price: float) -> bool:
        """更新移動停損 - 檢查是否啟動移動停損並更新停損價格"""
        if not self.current_position:
            return False

        # 如果還沒有啟動移動停損，檢查是否達到啟動條件
        if not self.trailing_stop_active:
            # 檢查是否達到啟動移動停損的條件
            if current_price - self.entry_price >= self.start_trailing_stop_points:
                print(f"獲利{current_price - self.entry_price}點，啟動移動停損")
                self.trailing_stop_active = True
                # 立即設定移動停損價格
                self.stop_loss_price = current_price - self.trailing_stop_points
                print(f"移動停損已啟動，停損價格: {self.stop_loss_price}")
                return True
            return False

        # 如果已經啟動移動停損，更新停損價格
        # 計算新的停損價格
        new_stop_price = current_price - self.trailing_stop_points

        # 檢查是否需要更新停損價格
        if new_stop_price > self.stop_loss_price:
            self.stop_loss_price = new_stop_price
            print(f"移動停損價格更新: {new_stop_price}")
            self.record_service.update_stop_loss(
                self.current_position.sub_symbol,
                new_stop_price,
                self.trailing_stop_active,
            )
            return True

        return False

    def _place_market_order_and_wait(
        self,
        symbol: str,
        sub_symbol: str,
        action: str,
        order_type: str = "Open",  # "Open" 或 "Close"
    ) -> float | None:
        """
        下市價單並等待成交，整合下單、等待成交、更新持倉狀態

        Args:
            symbol: 商品代碼
            sub_symbol: 子商品代碼
            action: 買賣動作 ("Buy" 或 "Sell")
            order_type: 訂單類型 ("Open" 開倉 或 "Close" 平倉)

        Returns:
            float | None: 成交價格，失敗時回傳 None
        """
        try:
            # 設定 octype
            octype = "Cover" if order_type == "Close" else "Auto"

            print(f"下市價單: {action} {order_type}")

            # 下市價單
            result = self.order_service.place_order(
                symbol=symbol,
                sub_symbol=sub_symbol,
                action=action,
                quantity=self.order_quantity,
                price_type="MKT",
                # order_type=None 會自動選擇 IOC 用於市價單
                octype=octype,
            )
            # 檢查下單是否成功
            if result.status == "Error":
                print(f"下單失敗: {result.msg}")
                time.sleep(60)
                return None

            print(f"下單成功: {action} {order_type}")

            # 等待成交
            start_time = datetime.now()
            timeout_minutes = 5

            while datetime.now() - start_time < timedelta(minutes=timeout_minutes):
                trades = self.order_service.check_order_status(
                    symbol=symbol,
                    sub_symbol=self._convert_sub_symbol_to_contract_code(sub_symbol),
                )
                filled_trades = [
                    t
                    for t in trades
                    if t.status.status in ["Filled", "PartFilled", "Status.Filled"]
                ]

                if filled_trades:
                    print(f"成交確認: {action} {order_type}")

                    # 等待一下讓系統更新
                    time.sleep(2)

                    # 更新持倉狀態
                    self.current_position = self._get_current_position(sub_symbol)
                    print(f"持倉狀態已更新: {action}")

                    # 根據成交時間取最新的交易記錄
                    latest_trade = self._get_latest_trade(filled_trades)
                    if latest_trade and latest_trade.status.deals:
                        # 取最後一筆成交的價格
                        last_deal = latest_trade.status.deals[-1]
                        fill_price = last_deal.price
                        print(f"成交價格: {fill_price} (成交時間: {last_deal.time})")
                        return fill_price
                    else:
                        print("警告: 未找到成交價格資訊")
                        return None

                time.sleep(1)

            print(f"等待成交超時: {action} {order_type}")
            return None

        except Exception as e:
            print(f"下單或等待成交失敗: {str(e)}")
            return None

    def run_strategy(self, symbol: str, sub_symbol: str):
        """執行策略循環 - 支持自適應檢測頻率"""
        print(f"開始交易策略: {symbol} {sub_symbol}")

        print("首次啟動，同步持倉狀態...")
        self.current_position = self._get_current_position(sub_symbol)

        # 如果有現有持倉，初始化停損信息
        if self.current_position:
            print(
                f"發現現有持倉: {self.current_position.direction} {self.current_position.quantity} @ {self.current_position.price}"
            )
            self._initialize_existing_position(symbol, sub_symbol)
        else:
            # 清理可能不同步的本地記錄（不記錄到 Google Sheets）
            self.record_service._remove_position_without_log(sub_symbol)

        # 按固定間隔執行策略
        print_flag = False
        while True:
            try:
                current_time = datetime.now()

                # 取得即時報價
                quote = self.market_service.get_futures_realtime_quote(
                    symbol, sub_symbol
                )
                if not quote:
                    print("無法取得即時報價")
                    if self.current_position:
                        wait_seconds(self.position_check_interval)
                    else:
                        calculate_and_wait_to_next_execution(
                            current_time, self.signal_check_interval, True
                        )
                    continue

                current_price = quote.price

                if self.current_position:
                    # 檢查停損觸發
                    if self._check_close_position_trigger(
                        symbol, sub_symbol, current_price
                    ):
                        calculate_and_wait_to_next_execution(
                            current_time, self.signal_check_interval, True
                        )
                        continue  # 停損觸發，不用更新trailing_stop
                    # 更新移動停損
                    self._update_trailing_stop(current_price)

                    if current_time.minute % 5 == 0 and not print_flag:
                        print_flag = True
                        print(
                            f"[{current_time.strftime('%H:%M:%S')}] 當前價格: {current_price:.1f}"
                        )
                    elif current_time.minute % 5 != 0:
                        print_flag = False

                    # 有持倉時，高頻檢測停損
                    wait_seconds(self.position_check_interval)

                else:
                    print(
                        f"\n[{current_time.strftime('%H:%M:%S')}] 當前價格: {current_price:.1f}"
                    )
                    # 取得K線資料
                    kbars_30m = self.market_service.get_futures_kbars_with_timeframe(
                        symbol, sub_symbol, "30m", days=30
                    )
                    input_data = StrategyInput(
                        symbol=sub_symbol,
                        kbars=kbars_30m,
                        current_price=current_price,
                        timestamp=datetime.now(),
                    )
                    signal = self.strategy_service.generate_signal(input_data)
                    # 檢查是否有交易訊號
                    if signal.action == Action.Buy:
                        print(f"收到交易訊號: {signal.action}")

                        # 使用整合函數下市價單開倉
                        fill_price = self._place_market_order_and_wait(
                            symbol, sub_symbol, signal.action, "Open"
                        )
                        if fill_price is not None:
                            # 設定停損點位
                            if self.current_position:
                                self.entry_price = fill_price  # 使用實際成交價格
                                self.trailing_stop_active = False
                                try:
                                    lowest_price = min(
                                        kbar.low for kbar in kbars_30m[-31:]
                                    )
                                except Exception as e:
                                    print(f"取得前31根K線最低點失敗: {str(e)}")
                                    lowest_price = fill_price - 50

                                self.stop_loss_price = (
                                    lowest_price - self.stop_loss_points
                                )
                                print(f"開倉成交價格: {fill_price}")
                                print(f"前30根K線最低點: {lowest_price}")
                                print(f"停損點位已設定: {self.stop_loss_price}")

                                # 保存持倉記錄到本地
                                position_record = PositionRecord(
                                    symbol=symbol,
                                    sub_symbol=sub_symbol,
                                    direction=signal.action,
                                    quantity=self.order_quantity,
                                    entry_price=fill_price,
                                    entry_time=datetime.now(),
                                    stop_loss_price=self.stop_loss_price,
                                    timeframe=self.timeframe,
                                    trailing_stop_active=False,
                                )
                                self.record_service.save_position(position_record)

                        else:
                            print("開倉失敗")
                            time.sleep(60)
                    else:
                        print("無交易訊號")
                        # 無持倉時，對齊時間等待
                        calculate_and_wait_to_next_execution(
                            current_time, self.signal_check_interval, True
                        )

            except KeyboardInterrupt:
                print("\n程式被使用者中斷")
                break
            except Exception as e:
                print(f"執行錯誤: {str(e)}")
                time.sleep(60)  # 發生錯誤時等待1分鐘再繼續
