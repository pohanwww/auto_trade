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
from auto_trade.services.line_bot_service import LineBotService
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
        line_bot_service: LineBotService = None,
    ):
        self.api_client = api_client
        self.account_service = account_service
        self.market_service = market_service
        self.order_service = order_service
        self.strategy_service = strategy_service
        self.line_bot_service = line_bot_service

        # 記錄服務（自動從 Config 讀取 Google Sheets 設定）
        self.record_service = RecordService()

        # 交易狀態追蹤
        self.current_position: FuturePosition | None = None
        self.entry_price: int = 0
        self.trailing_stop_active: bool = False
        self.stop_loss_price: int = 0  # 停損價格 (共用於初始停損和移動停損)
        self.last_sync_time: datetime | None = None
        self.is_in_macd_death_cross: bool = False  # MACD 死叉狀態追蹤
        self.last_fast_stop_check_kbar_time: datetime | None = (
            None  # 最後檢查快速停損的 K 棒時間
        )

        # 交易參數 (預設值)
        self.trailing_stop_points: int = 200
        self.trailing_stop_points_rate: float | None = None
        self.start_trailing_stop_points: int = 200
        self.order_quantity: int = 1
        self.stop_loss_points: int = 50
        self.take_profit_points: int = 500
        self.take_profit_points_rate: float | None = None
        self.timeframe: str = "30m"  # K線時間尺度

        # 檢測頻率參數
        self.signal_check_interval: int = 5  # 訊號檢測間隔 (分鐘)
        self.position_check_interval: int = 5  # 持倉檢測間隔 (秒)

        # 交易商品信息
        self.symbol: str | None = None
        self.sub_symbol: str | None = None
        self.contract_code: str | None = None

    def set_trading_params(self, params: dict):
        """設定交易參數"""
        self.trailing_stop_points = params.get("trailing_stop_points", 200)
        self.trailing_stop_points_rate = params.get("trailing_stop_points_rate")
        self.start_trailing_stop_points = params.get("start_trailing_stop_points", 200)
        self.order_quantity = params.get("order_quantity", 1)
        self.stop_loss_points = params.get("stop_loss_points", 50)
        self.take_profit_points = params.get("take_profit_points", 500)
        self.take_profit_points_rate = params.get("take_profit_points_rate")
        self.timeframe = params.get("timeframe", "30m")

        # 檢測頻率參數
        self.signal_check_interval = params.get("signal_check_interval", 5)
        self.position_check_interval = params.get("position_check_interval", 5)

        # 處理 symbol 和 sub_symbol
        self.symbol = params.get("symbol")
        self.sub_symbol = params.get("sub_symbol")

        if self.symbol and self.sub_symbol:
            # 直接獲取合約代碼
            try:
                # 如果 sub_symbol 是字符串，使用原來的查找邏輯
                product_info = self.market_service.get_futures_product_info(self.symbol)
                if product_info and "contracts" in product_info:
                    contracts = product_info["contracts"]
                    # 現在 contracts 的 key 是 sub_symbol，直接查找
                    if self.sub_symbol in contracts:
                        contract_info = contracts[self.sub_symbol]
                        self.contract_code = contract_info.get("code")
                        print(
                            f"✅ 設置合約代碼: {self.sub_symbol} → {self.contract_code}"
                        )
                    else:
                        print(
                            f"⚠️ 在 {self.symbol} 中找不到 sub_symbol: {self.sub_symbol}"
                        )
                else:
                    print(f"⚠️ 無法獲取 {self.symbol} 的商品信息")
            except Exception as e:
                print(f"❌ 獲取合約代碼失敗: {e}")

        print("交易參數已設定:")
        if self.symbol:
            print(f"  商品代碼: {self.symbol}")
        if self.sub_symbol:
            print(f"  子商品代碼: {self.sub_symbol}")
        if self.contract_code:
            print(f"  合約代碼: {self.contract_code}")
        trailing_stop_display = (
            f"{self.trailing_stop_points_rate * 100}% (進入價格 × {self.trailing_stop_points_rate})"
            if self.trailing_stop_points_rate is not None
            else f"{self.trailing_stop_points} 點"
        )
        take_profit_display = (
            f"{self.take_profit_points_rate * 100}% (進入價格 × {self.take_profit_points_rate})"
            if self.take_profit_points_rate is not None
            else f"{self.take_profit_points} 點"
        )
        print(f"  移動停損: {trailing_stop_display}")
        print(f"  啟動移動停損點數: {self.start_trailing_stop_points}")
        print(f"  下單數量: {self.order_quantity}")
        print(f"  初始停損點數: {self.stop_loss_points}")
        print(f"  獲利了結: {take_profit_display}")
        print(f"  K線時間尺度: {self.timeframe}")
        print(f"  訊號檢測間隔: {self.signal_check_interval} 分鐘")
        print(f"  持倉檢測間隔: {self.position_check_interval} 秒")
        print("  MACD 快速停損強度門檻: 3.0")

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

    def _calculate_trailing_stop_points(self, entry_price: int) -> int:
        """根據進入價格計算移動停損點數"""
        if self.trailing_stop_points_rate is not None:
            return int(entry_price * self.trailing_stop_points_rate)
        return int(self.trailing_stop_points)

    def _calculate_take_profit_points(self, entry_price: int) -> int:
        """根據進入價格計算獲利了結點數"""
        if self.take_profit_points_rate is not None:
            return int(entry_price * self.take_profit_points_rate)
        return int(self.take_profit_points)

    def _calculate_trailing_stop_from_history(
        self, symbol: str, sub_symbol: str, entry_time: datetime, entry_price: int
    ) -> tuple[int, bool]:
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

        # 直接獲取指定時間尺度的 K 棒數據
        kbars_30m = self.market_service.get_futures_kbars_with_timeframe(
            symbol, sub_symbol, self.timeframe, days_diff
        )

        if not kbars_30m or len(kbars_30m.kbars) < 30:
            raise ValueError(
                f"歷史數據不足: 需要至少 30 根{self.timeframe}K棒，實際獲得 {len(kbars_30m.kbars) if kbars_30m else 0} 根"
            )

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
            trailing_stop_points = self._calculate_trailing_stop_points(entry_price)
            trailing_stop_loss = highest_price - trailing_stop_points
            print(
                f"✅ 移動停損已啟動，停損價格: {trailing_stop_loss:.1f} (點數: {trailing_stop_points:.1f})"
            )
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

                take_profit_points = self._calculate_take_profit_points(
                    self.entry_price
                )
                print(
                    f"獲利了結價格: {self.entry_price + take_profit_points:.1f} (點數: {take_profit_points:.1f})"
                )

                # 恢復 MACD 死叉狀態
                self._restore_macd_death_cross_status()

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

                # 使用合約代碼查詢
                print(f"使用合約代碼: {self.contract_code}")

                trades = self.order_service.check_order_status(
                    symbol=symbol, sub_symbol=self.contract_code
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
            take_profit_points = self._calculate_take_profit_points(self.entry_price)
            take_profit_price = self.entry_price + take_profit_points

            print(
                f"獲利了結價格: {take_profit_price:.1f} (點數: {take_profit_points:.1f})"
            )
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

            # 恢復 MACD 死叉狀態
            self._restore_macd_death_cross_status()

            print("現有持倉初始化完成 (使用備用方案)")

        except Exception as e:
            print(f"初始化現有持倉失敗: {e}")

    def _get_current_position(self, sub_symbol: str) -> FuturePosition | None:
        """取得當前持倉"""
        try:
            positions = self.account_service.get_future_positions()
            print(
                f"查找持倉: sub_symbol={sub_symbol} → contract_code={self.contract_code}"
            )

            for pos in positions:
                print(f"檢查持倉: code={pos.code}, quantity={pos.quantity}")
                if pos.code == self.contract_code and pos.quantity != 0:
                    # 設定 sub_symbol 以便後續使用
                    pos.sub_symbol = sub_symbol
                    print(f"找到持倉: {pos}")
                    return pos
            return None
        except Exception as e:
            print(f"取得持倉失敗: {str(e)}")
            return None

    def _restore_macd_death_cross_status(self) -> None:
        """恢復 MACD 死叉狀態（程式重啟時使用）

        檢查從開倉到現在的時間線中，最後一個死叉是否為強死叉，
        如果是強死叉且之後沒有金叉，則設置 is_in_macd_death_cross = True
        """
        try:
            # 如果移動停損已啟動，不需要檢查 MACD 狀態
            if self.trailing_stop_active:
                print("✅ 移動停損已啟動，不需要檢查 MACD 快速停損狀態")
                return

            print("🔍 檢查從開倉到現在的 MACD 死叉狀態...")

            # 獲取 K 線數據（需要足夠的數據來計算 MACD）
            kbars_30m = self.market_service.get_futures_kbars_with_timeframe(
                self.symbol, self.sub_symbol, self.timeframe, days=30
            )

            if not kbars_30m or len(kbars_30m.kbars) < 35:
                print("⚠️  K 線數據不足，無法檢查 MACD 狀態")
                return

            # 使用 strategy_service 計算 MACD
            macd_list = self.strategy_service.calculate_macd(kbars_30m)

            if len(macd_list.macd_data) < 2:
                print("⚠️  MACD 數據不足")
                return

            # 遍歷 MACD 數據，找到最後一次死叉和金叉
            last_death_cross_idx = None
            last_death_cross_strength = 0.0
            last_golden_cross_idx = None

            for i in range(1, len(macd_list.macd_data)):
                current = macd_list.macd_data[i]
                previous = macd_list.macd_data[i - 1]

                # 檢測死叉（MACD 從上方穿過 Signal 向下）
                if (
                    previous.macd_line >= previous.signal_line
                    and current.macd_line < current.signal_line
                ):
                    last_death_cross_idx = i
                    last_death_cross_strength = abs(
                        current.macd_line - current.signal_line
                    )
                    print(
                        f"   發現死叉 @ K棒 {i}（強度 {last_death_cross_strength:.2f}）- "
                        f"MACD: {current.macd_line:.2f}, Signal: {current.signal_line:.2f}"
                    )

                # 檢測金叉（MACD 從下方穿過 Signal 向上）
                elif (
                    previous.macd_line <= previous.signal_line
                    and current.macd_line > current.signal_line
                ):
                    last_golden_cross_idx = i
                    print(
                        f"   發現金叉 @ K棒 {i} - "
                        f"MACD: {current.macd_line:.2f}, Signal: {current.signal_line:.2f}"
                    )

            # 判斷是否應該恢復死叉狀態
            if last_death_cross_idx is not None:
                # 如果最後一次死叉之後沒有金叉（或金叉在死叉之前）
                if (
                    last_golden_cross_idx is None
                    or last_golden_cross_idx < last_death_cross_idx
                ):
                    # 檢查死叉強度
                    if last_death_cross_strength > 3.0:
                        self.is_in_macd_death_cross = True
                        current = macd_list.macd_data[last_death_cross_idx]
                        kbars_ago = len(macd_list.macd_data) - last_death_cross_idx - 1
                        print(f"🔴 恢復強死叉狀態！最後死叉在 {kbars_ago} 根 K 棒前")
                        print(
                            f"   強度: {last_death_cross_strength:.2f}, "
                            f"MACD: {current.macd_line:.2f}, Signal: {current.signal_line:.2f}"
                        )
                    else:
                        print(
                            f"⚪ 最後一次死叉為弱死叉（強度 {last_death_cross_strength:.2f} <= 3.0），不恢復監控狀態"
                        )
                else:
                    print("✅ 最後一次死叉後已有金叉，無需恢復死叉狀態")
            else:
                print("✅ 未發現死叉，無需恢復死叉狀態")

        except Exception as e:
            print(f"⚠️  檢查 MACD 狀態失敗: {e}")

    def _check_macd_fast_stop(self, current_price: int) -> bool:
        """檢查 MACD 快速停損（只在新 K 棒出現時執行）

        只在以下情況檢查：
        1. 當前虧損 >= stop_loss_points（需要開始監控）
        2. 已在死叉狀態（需要追蹤金叉來解除狀態）

        Args:
            current_price: 當前價格

        Returns:
            bool: 是否觸發快速停損
        """
        try:
            # 計算當前盈虧
            current_profit = current_price - self.entry_price

            # 如果盈利或虧損未達門檻，且不在死叉狀態，不需要檢查
            if (
                current_profit >= -self.stop_loss_points
                and not self.is_in_macd_death_cross
            ):
                return False

            # 先獲取 K 線數據來檢查是否有新 K 棒
            kbars_30m = self.market_service.get_futures_kbars_with_timeframe(
                self.symbol, self.sub_symbol, self.timeframe, days=30
            )

            if not kbars_30m or len(kbars_30m.kbars) < 35:
                return False

            # 獲取最新 K 棒的時間
            latest_kbar = kbars_30m.kbars[-1]
            latest_kbar_time = latest_kbar.ts

            # 如果是同一根 K 棒，不重複檢查
            if self.last_fast_stop_check_kbar_time == latest_kbar_time:
                return False

            # 新 K 棒出現，執行快速停損檢查
            print(f"🆕 檢測到新 K 棒（{latest_kbar_time}），檢查 MACD 快速停損...")
            self.last_fast_stop_check_kbar_time = latest_kbar_time

            # 如果已經在死叉狀態且虧損達標，立即觸發快速停損
            if (
                self.is_in_macd_death_cross
                and not self.trailing_stop_active
                and current_profit < -self.stop_loss_points
            ):
                print(
                    f"⚡ MACD 快速停損觸發！虧損 {-current_profit:.1f} 點 >= 門檻 {self.stop_loss_points} 點"
                )
                return True

            # 計算 MACD 並檢查死叉/金叉（無論是否已在死叉狀態，都要檢查金叉來解除狀態）
            # 使用 strategy_service 計算 MACD
            macd_list = self.strategy_service.calculate_macd(kbars_30m)

            # 使用 strategy_service 檢測死叉和金叉
            is_death_cross = self.strategy_service.check_death_cross(macd_list)
            is_golden_cross = self.strategy_service.check_golden_cross(macd_list)

            # 獲取當前 MACD 值用於日誌輸出
            latest_macd = macd_list.get_latest(1)
            if latest_macd:
                current_macd_data = latest_macd[-1]
                current_macd = current_macd_data.macd_line
                current_signal = current_macd_data.signal_line
            else:
                return False

            # 死叉確認
            if is_death_cross:
                # 檢查死叉強度（強度定義為 MACD 與 Signal 的差距）
                death_cross_strength = abs(current_macd - current_signal)

                # 只有強死叉（強度 > 3）才進入監控
                if death_cross_strength > 3.0:
                    self.is_in_macd_death_cross = True
                    print(
                        f"🔴 強死叉確認（強度 {death_cross_strength:.2f}）- MACD: {current_macd:.2f}, Signal: {current_signal:.2f}"
                    )

                    # 檢查是否達到虧損門檻
                    if (
                        not self.trailing_stop_active
                        and current_profit < -self.stop_loss_points
                    ):
                        print(
                            f"⚡ MACD 快速停損觸發！虧損 {-current_profit:.1f} 點 >= 門檻 {self.stop_loss_points} 點"
                        )
                        return True
                else:
                    # 弱死叉 - 忽略
                    print(
                        f"⚪ 弱死叉（強度 {death_cross_strength:.2f} <= 3.0）- 忽略，等待初始停損"
                    )

            # 金叉確認 - 解除死叉狀態
            elif is_golden_cross:
                if self.is_in_macd_death_cross:
                    self.is_in_macd_death_cross = False
                    print(
                        f"✅ MACD 金叉，解除死叉狀態 (MACD: {current_macd:.2f}, Signal: {current_signal:.2f})"
                    )

            return False

        except Exception as e:
            print(f"⚠️  MACD 快速停損檢查失敗: {e}")
            return False

    def _update_trailing_stop(self, current_price: int) -> bool:
        """更新移動停損 - 檢查是否啟動移動停損並更新停損價格"""
        if not self.current_position:
            return False

        if not self.trailing_stop_active:
            if current_price - self.entry_price >= self.start_trailing_stop_points:
                print(f"獲利{current_price - self.entry_price}點，啟動移動停損")
                self.trailing_stop_active = True
                # 立即設定移動停損價格
                trailing_stop_points = self._calculate_trailing_stop_points(
                    self.entry_price
                )
                self.stop_loss_price = current_price - trailing_stop_points
                print(
                    f"移動停損已啟動，停損價格: {self.stop_loss_price} (點數: {trailing_stop_points:.1f})"
                )

                # 更新本地記錄
                self.record_service.update_stop_loss(
                    self.current_position.sub_symbol,
                    self.stop_loss_price,
                    self.trailing_stop_active,
                )
                return True
            return False

        trailing_stop_points = self._calculate_trailing_stop_points(self.entry_price)
        new_stop_price = current_price - trailing_stop_points
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
                    result.order_id,
                )
                if trades and trades[0].status.status in [
                    "Filled",
                    "PartFilled",
                    "Status.Filled",
                ]:
                    current_trade = trades[0]
                    print(f"成交確認: {action} {order_type}")
                    time.sleep(2)  # 等待一下讓系統更新

                    # 更新持倉狀態
                    self.current_position = self._get_current_position(sub_symbol)
                    print(f"持倉狀態已更新: {action}")

                    if current_trade.status.deals:
                        last_deal = current_trade.status.deals[-1]
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

    def run_strategy(self):
        """執行策略循環 - 支持自適應檢測頻率"""
        # 早期失敗檢查
        if not all([self.symbol, self.sub_symbol, self.contract_code]):
            print(
                f"❌ 錯誤: 未設置 {', '.join([k for k, v in {'symbol': self.symbol, 'sub_symbol': self.sub_symbol, 'contract_code': self.contract_code}.items() if not v])}，請先調用 set_trading_params"
            )
            return

        print(
            f"開始交易策略: {self.symbol} {self.sub_symbol} (合約代碼: {self.contract_code})"
        )

        print("首次啟動，同步持倉狀態...")
        self.current_position = self._get_current_position(self.sub_symbol)

        # 如果有現有持倉，初始化停損信息
        if self.current_position:
            print(
                f"發現現有持倉: {self.current_position.direction} {self.current_position.quantity} @ {self.current_position.price}"
            )
            self._initialize_existing_position(self.symbol, self.sub_symbol)
        else:
            # 清理可能不同步的本地記錄（不記錄到 Google Sheets）
            self.record_service._remove_position_without_log(self.sub_symbol)

        # 按固定間隔執行策略
        print_flag = False
        while True:
            try:
                current_time = datetime.now()

                # 取得即時報價
                quote = self.market_service.get_futures_realtime_quote(
                    self.symbol, self.sub_symbol
                )
                if not quote:
                    raise Exception("無法取得即時報價")

                current_price = quote.price

                if self.current_position:
                    current_profit = current_price - self.entry_price

                    # 檢查 MACD 快速停損（內部自動判斷是否需要檢查）
                    fast_stop_triggered = self._check_macd_fast_stop(current_price)

                    # 檢查其他停損條件
                    stop_triggered = current_price <= self.stop_loss_price
                    take_profit_points = self._calculate_take_profit_points(
                        self.entry_price
                    )
                    profit_triggered = current_profit >= take_profit_points

                    if (
                        fast_stop_triggered or stop_triggered or profit_triggered
                    ):  # 檢查是否觸發停損或獲利了結
                        # 平倉（賣出）
                        fill_price = self._place_market_order_and_wait(
                            self.symbol, self.sub_symbol, Action.Sell, "Close"
                        )
                        if fill_price is not None:
                            # 判斷退出原因
                            if profit_triggered:
                                exit_reason = ExitReason.TAKE_PROFIT
                            elif fast_stop_triggered:
                                exit_reason = ExitReason.FAST_STOP
                                print(f"⚡ MACD 快速停損執行，成交價格: {fill_price}")
                            elif self.trailing_stop_active:
                                exit_reason = ExitReason.TRAILING_STOP
                            else:
                                exit_reason = ExitReason.STOP_LOSS

                            print(f"觸發平倉，成交價格: {fill_price}")

                            # 移除本地持倉記錄並記錄平倉資訊
                            self.record_service.remove_position(
                                self.sub_symbol,
                                fill_price,
                                exit_reason,
                                {
                                    "stop_loss_points": self.stop_loss_points,
                                    "start_trailing_stop_points": self.start_trailing_stop_points,
                                    "trailing_stop_points": self._calculate_trailing_stop_points(
                                        self.entry_price
                                    ),
                                    "take_profit_points": self._calculate_take_profit_points(
                                        self.entry_price
                                    ),
                                    "trailing_stop_points_rate": self.trailing_stop_points_rate,
                                    "take_profit_points_rate": self.take_profit_points_rate,
                                },
                            )

                            # 重置狀態
                            self.current_position = None
                            self.trailing_stop_active = False
                            self.stop_loss_price = 0.0
                            self.entry_price = 0.0
                            self.is_in_macd_death_cross = False  # 重置 MACD 死叉狀態
                            self.last_fast_stop_check_kbar_time = (
                                None  # 重置 K 棒檢查時間
                            )

                            # 獲取 Google Sheets 最新數據並發送 Line 通知
                            if self.line_bot_service:
                                try:
                                    latest_data = (
                                        self.record_service.get_latest_row_data(
                                            "交易記錄"
                                        )
                                    )
                                    if latest_data:
                                        self.line_bot_service.send_close_position_message(
                                            symbol=self.symbol,
                                            sub_symbol=self.sub_symbol,
                                            price=fill_price,
                                            exit_reason=exit_reason.value,
                                            latest_data=latest_data,
                                        )
                                except Exception as e:
                                    print(f"❌ 發送平倉通知失敗: {e}")

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
                    kbars_30m = self.market_service.get_futures_kbars_with_timeframe(
                        self.symbol, self.sub_symbol, "30m", days=30
                    )
                    signal = self.strategy_service.generate_signal(
                        StrategyInput(
                            symbol=self.sub_symbol,
                            kbars=kbars_30m,
                            current_price=current_price,
                            timestamp=datetime.now(),
                            stop_loss_points=self.stop_loss_points,
                        )
                    )
                    if signal.action == Action.Buy:
                        print(f"收到交易訊號: {signal.action}")
                        fill_price = self._place_market_order_and_wait(
                            self.symbol, self.sub_symbol, signal.action, "Open"
                        )
                        if fill_price is not None and self.current_position:
                            self.entry_price = fill_price
                            self.trailing_stop_active = False
                            self.stop_loss_price = signal.stop_loss_price
                            self.is_in_macd_death_cross = False  # 重置 MACD 死叉狀態
                            self.last_fast_stop_check_kbar_time = (
                                None  # 重置 K 棒檢查時間
                            )

                            print(f"開倉成交價格: {fill_price}")
                            print(f"停損點位已設定: {self.stop_loss_price}")

                            self.record_service.save_position(
                                PositionRecord(
                                    symbol=self.symbol,
                                    sub_symbol=self.sub_symbol,
                                    direction=signal.action,
                                    quantity=self.order_quantity,
                                    entry_price=fill_price,
                                    entry_time=datetime.now(),
                                    stop_loss_price=self.stop_loss_price,
                                    timeframe=self.timeframe,
                                    trailing_stop_active=False,
                                )
                            )

                            if self.line_bot_service:
                                self.line_bot_service.send_open_position_message(
                                    symbol=self.symbol,
                                    sub_symbol=self.sub_symbol,
                                    price=fill_price,
                                    quantity=self.order_quantity,
                                    action=signal.action,
                                    stop_loss_price=self.stop_loss_price,
                                )
                        else:
                            print("開倉失敗, 等待60秒後重試")
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
                print("結束程式")
                break
