"""Backtest Engine - 回測引擎.

使用與實盤完全相同的 Strategy + PositionManager 邏輯，
只差在 Executor 是 BacktestExecutor（模擬成交）。

支持：
- 單一 TradingUnit 回測
- 多 TradingUnit 組合回測
- 自動產生回測報告
- Buy & Hold 基準比較
- 權益曲線圖表
- 做多和做空方向
"""

import os
import uuid
from datetime import datetime

from auto_trade.executors.backtest_executor import BacktestExecutor
from auto_trade.models.account import Action
from auto_trade.models.backtest import (
    BacktestResult,
    BacktestTrade,
    get_point_value,
)
from auto_trade.models.market import KBarList
from auto_trade.models.position_record import ExitReason
from auto_trade.models.strategy import SignalType, StrategySignal
from auto_trade.models.trading_unit import TradingUnit
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.services.market_service import MarketService
from auto_trade.services.position_manager import PositionManager


class BacktestEngineConfig:
    """回測引擎配置"""

    # 商品對應的滾動合約映射（用於歷史資料取得）
    # TX 價格相同，TX 流動性較好
    ROLLING_CONTRACT_MAP: dict[str, tuple[str, str]] = {
        "TX": ("TX", "TXR1"),  # 大台 → 用大台近月滾動
    }

    def __init__(
        self,
        symbol: str,
        sub_symbol: str,
        start_date: datetime,
        end_date: datetime,
        timeframe: str = "30m",
        initial_capital: float = 1_000_000.0,
        slippage_points: int = 0,
        data_symbol: str | None = None,
        data_sub_symbol: str | None = None,
    ):
        self.symbol = symbol
        self.sub_symbol = sub_symbol
        self.start_date = start_date
        self.end_date = end_date
        self.timeframe = timeframe
        self.initial_capital = initial_capital
        self.slippage_points = slippage_points

        # 歷史資料來源（預設使用滾動合約）
        if data_symbol and data_sub_symbol:
            self.data_symbol = data_symbol
            self.data_sub_symbol = data_sub_symbol
        elif symbol in self.ROLLING_CONTRACT_MAP:
            self.data_symbol, self.data_sub_symbol = self.ROLLING_CONTRACT_MAP[symbol]
        else:
            # 其他商品預設用 {symbol}R1
            self.data_symbol = symbol
            self.data_sub_symbol = f"{symbol}R1"


class BacktestEngine:
    """回測引擎

    用法：
        engine = BacktestEngine(config, market_service, indicator_service)
        results = engine.run([trading_unit_1, trading_unit_2])
    """

    def __init__(
        self,
        config: BacktestEngineConfig,
        market_service: MarketService,
        indicator_service: IndicatorService,
    ):
        self.config = config
        self.market_service = market_service
        self.indicator_service = indicator_service
        # 保存 K 線資料（供基準比較和圖表使用）
        self._kbar_list: KBarList | None = None

    # 時間尺度排序（用於選擇最細粒度）
    _TIMEFRAME_MINUTES = {
        "1m": 1,
        "5m": 5,
        "10m": 10,
        "15m": 15,
        "30m": 30,
        "1h": 60,
    }

    def run(self, trading_units: list[TradingUnit]) -> dict[str, BacktestResult]:
        """執行多 TradingUnit 的回測

        支援不同 TradingUnit 使用不同的 timeframe：
        - 收集所有 unit 的 timeframe，取最細的作為資料來源
        - 對每個 unit 按需 resample 到其對應 timeframe

        Args:
            trading_units: 要回測的交易單元列表

        Returns:
            dict[unit_name, BacktestResult]: 每個 TradingUnit 的回測結果
        """
        # 收集所有需要的 timeframe
        needed_timeframes: set[str] = set()
        for unit in trading_units:
            if unit.enabled:
                needed_timeframes.add(unit.pm_config.timeframe)
        if not needed_timeframes:
            needed_timeframes.add(self.config.timeframe)

        # 找出最細的 timeframe 來取得資料
        finest_tf = min(
            needed_timeframes,
            key=lambda tf: self._TIMEFRAME_MINUTES.get(tf, 9999),
        )

        # 取得歷史數據（用最細粒度取一次）
        original_tf = self.config.timeframe
        self.config.timeframe = finest_tf
        kbar_list = self._get_historical_data()
        self.config.timeframe = original_tf  # 還原

        if not kbar_list or len(kbar_list) == 0:
            print("❌ 無法取得歷史數據")
            return {}

        self._kbar_list = kbar_list  # 保存供基準比較和圖表使用
        print(f"📊 取得 {len(kbar_list)} 根 K 線 ({finest_tf})")

        # 預先 resample 各 timeframe 版本（避免重複計算）
        kbar_cache: dict[str, KBarList] = {finest_tf: kbar_list}
        for tf in needed_timeframes:
            if tf != finest_tf:
                print(f"   ↳ Resample → {tf}")
                kbar_cache[tf] = self.market_service.resample_kbars(kbar_list, tf)

        results = {}
        for unit in trading_units:
            if not unit.enabled:
                continue

            unit_tf = unit.pm_config.timeframe
            unit_kbars = kbar_cache.get(unit_tf, kbar_list)

            print(f"\n{'=' * 60}")
            print(f"🚀 回測 TradingUnit: {unit.name}")
            print(f"   策略: {unit.strategy.name}")
            print(f"   時間尺度: {unit_tf} ({len(unit_kbars)} 根 K 線)")
            print(f"   配置: {unit.pm_config}")
            print(f"{'=' * 60}")

            result = self._run_single_unit(unit, unit_kbars)
            results[unit.name] = result

        return results

    def _run_single_unit(
        self, unit: TradingUnit, kbar_list: KBarList
    ) -> BacktestResult:
        """執行單一 TradingUnit 的回測

        進場邏輯（修正 Look-Ahead Bias）：
        - 策略在 bar[i] 的收盤價評估信號
        - 信號延遲至 bar[i+1] 的開盤價成交（Deferred Entry）
        - 所有出場參數（TP/TS/SL）以實際成交價為基準計算

        權益曲線：
        - 持倉期間追蹤未實現損益，確保 Max Drawdown 反映真實風險
        """
        executor = BacktestExecutor(slippage_points=self.config.slippage_points)
        pm = PositionManager(
            config=unit.pm_config,
            indicator_service=self.indicator_service,
        )

        from auto_trade.models.backtest import BacktestConfig

        bt_config = BacktestConfig(
            symbol=self.config.symbol,
            sub_symbol=self.config.sub_symbol,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            timeframe=unit.pm_config.timeframe,
            initial_capital=self.config.initial_capital,
            order_quantity=unit.pm_config.total_quantity,
            stop_loss_points=unit.pm_config.stop_loss_points,
            start_trailing_stop_points=unit.pm_config.start_trailing_stop_points,
            trailing_stop_points=unit.pm_config.trailing_stop_points,
            take_profit_points=unit.pm_config.take_profit_points,
            trailing_stop_points_rate=unit.pm_config.trailing_stop_points_rate,
            take_profit_points_rate=unit.pm_config.take_profit_points_rate,
            enable_macd_fast_stop=unit.pm_config.enable_macd_fast_stop,
        )

        result = BacktestResult(config=bt_config)
        result.equity_curve.append(
            (self.config.start_date, self.config.initial_capital)
        )
        current_equity = self.config.initial_capital
        point_value = get_point_value(self.config.symbol)

        # 追蹤開倉資訊（用於產生 BacktestTrade）
        pending_entry_price: int | None = None
        pending_entry_time: datetime | None = None
        pending_direction: Action | None = None

        # Deferred Entry：策略信號延遲到下一根 K 棒開盤成交
        _deferred_signal: StrategySignal | None = None
        _deferred_direction: Action | None = None

        total_qty = unit.pm_config.total_quantity

        for i in range(30, len(kbar_list)):
            kbar = kbar_list[i]
            current_time = kbar.time
            current_price = int(kbar.close)
            current_high = int(kbar.high)
            current_low = int(kbar.low)
            current_open = int(kbar.open)

            executor.set_market_state(current_open, current_time)

            current_kbars = kbar_list.view(i + 1)

            # ── Step 1: 處理延遲進場（上一根 K 棒的信號，用本根 Open 成交）──
            if _deferred_signal is not None:
                fill_price = current_open
                _deferred_signal.price = float(fill_price)
                is_addon = pm.has_position

                actions = pm.on_signal(
                    _deferred_signal,
                    current_kbars,
                    self.config.symbol,
                    self.config.sub_symbol,
                )

                for action in actions:
                    executor.set_market_state(fill_price, current_time)
                    fill = executor.execute(action)

                    if fill.success and fill.fill_price is not None:
                        if not is_addon and pm.position:
                            pm.position.entry_price = fill.fill_price
                            pm.position.entry_time = current_time
                            pm.position.highest_price = fill.fill_price
                            pm.position.lowest_price = fill.fill_price

                        if not is_addon:
                            pending_entry_price = fill.fill_price
                            pending_entry_time = current_time
                            pending_direction = _deferred_direction
                            dir_str = "做多" if pending_direction == Action.Buy else "做空"
                            print(f"📈 {dir_str}開倉: {fill.fill_price}")
                        else:
                            print(f"➕ 加碼成交: {fill.fill_price} x{action.quantity}")

                _deferred_signal = None
                _deferred_direction = None

            # ── Step 2: 持倉中 → 檢查出場條件 ──
            if pm.has_position:
                is_long = pm.position.direction == Action.Buy

                pm.position.update_price_tracking(current_high)
                pm.position.update_price_tracking(current_low)

                # 0. 時間強制平倉（日內策略用）
                actions = pm.check_time_exit(current_time, current_price)

                # 1. Open 檢查跳空觸發
                if not actions:
                    actions = pm.on_price_update(current_open, current_kbars)

                # 2. 方向性極端價檢查
                if not actions:
                    if is_long:
                        actions = pm.on_price_update(current_low, current_kbars)
                        if not actions:
                            actions = pm.on_price_update(current_high, current_kbars)
                    else:
                        actions = pm.on_price_update(current_high, current_kbars)
                        if not actions:
                            actions = pm.on_price_update(current_low, current_kbars)

                # 3. 收盤價更新狀態
                if not actions:
                    pm.on_price_update(current_price, current_kbars)

                # 執行平倉
                if actions:
                    eq_delta, closed = self._execute_exit_actions(
                        actions,
                        pm=pm,
                        executor=executor,
                        result=result,
                        current_time=current_time,
                        kbar_open=current_open,
                        kbar_high=current_high,
                        kbar_low=current_low,
                        kbar_close=current_price,
                        is_long=is_long,
                        pending_entry_price=pending_entry_price,
                        pending_entry_time=pending_entry_time,
                        pending_direction=pending_direction,
                        point_value=point_value,
                    )
                    current_equity += eq_delta
                    if closed:
                        pending_entry_price = None
                        pending_entry_time = None
                        pending_direction = None
                        unit.strategy.on_position_closed(bar_time=current_time)

            # ── Step 2.5: 持倉中 + 加碼啟用 → 評估加碼信號 ──
            if (
                pm.has_position
                and pm.config.enable_addon
                and _deferred_signal is None
            ):
                signal = unit.strategy.evaluate(
                    current_kbars, current_price, self.config.sub_symbol
                )
                pos = pm.position
                is_same_dir = (
                    (signal.signal_type == SignalType.ENTRY_LONG and pos.direction == Action.Buy)
                    or (signal.signal_type == SignalType.ENTRY_SHORT and pos.direction == Action.Sell)
                )
                if is_same_dir:
                    _deferred_signal = signal
                    _deferred_direction = pos.direction
                    print(
                        f"🔔 加碼信號 @ {current_price} "
                        f"(延遲至下一根 K 棒開盤進場)"
                    )

            elif _deferred_signal is None and not pm.has_position:
                # ── Step 3: 無倉位且無待處理信號 → 評估策略 ──
                signal = unit.strategy.evaluate(
                    current_kbars, current_price, self.config.sub_symbol
                )

                if signal.signal_type in (
                    SignalType.ENTRY_LONG,
                    SignalType.ENTRY_SHORT,
                ):
                    _entry_direction = (
                        Action.Buy
                        if signal.signal_type == SignalType.ENTRY_LONG
                        else Action.Sell
                    )

                    if signal.metadata.get("instant_entry"):
                        # ── Instant entry: 當根 K 棒立即成交 ──
                        instant_price = max(
                            current_low,
                            min(current_high, int(signal.price)),
                        )
                        signal.price = float(instant_price)

                        entry_actions = pm.on_signal(
                            signal, current_kbars,
                            self.config.symbol, self.config.sub_symbol,
                        )

                        for action in entry_actions:
                            executor.set_market_state(instant_price, current_time)
                            fill = executor.execute(action)

                            if fill.success and fill.fill_price is not None:
                                if pm.position:
                                    pm.position.entry_price = fill.fill_price
                                    pm.position.entry_time = current_time
                                    pm.position.highest_price = fill.fill_price
                                    pm.position.lowest_price = fill.fill_price

                                pending_entry_price = fill.fill_price
                                pending_entry_time = current_time
                                pending_direction = _entry_direction
                                dir_str = "做多" if _entry_direction == Action.Buy else "做空"
                                print(f"⚡ {dir_str}即時開倉: {fill.fill_price}")

                        # Skip same-bar exit checks for instant entry.
                        # The bar's high/low partially occurred before entry,
                        # so using them for SL/TS would introduce look-ahead.
                        # Exit checks begin from the next bar onward.
                        if pm.has_position:
                            pm.on_price_update(current_price, current_kbars)

                    else:
                        # ── Deferred entry: 延遲到下一根 K 棒開盤 ──
                        _deferred_signal = signal
                        _deferred_direction = _entry_direction
                        dir_str = "做多" if _deferred_direction == Action.Buy else "做空"
                        print(
                            f"🔔 {dir_str}信號 @ {current_price} "
                            f"(延遲至下一根 K 棒開盤進場)"
                        )

            # ── Step 4: 更新權益曲線（含未實現損益）──
            if pm.has_position and pending_entry_price is not None:
                open_qty = pm.position.open_quantity if pm.position else total_qty
                if pending_direction == Action.Buy:
                    unrealized = (
                        (current_price - pending_entry_price) * open_qty * point_value
                    )
                else:
                    unrealized = (
                        (pending_entry_price - current_price) * open_qty * point_value
                    )
                result.equity_curve.append((current_time, current_equity + unrealized))
            else:
                result.equity_curve.append((current_time, current_equity))

        # 計算統計
        result.calculate_statistics()
        result.backtest_duration_days = (
            self.config.end_date - self.config.start_date
        ).days

        print(
            f"\n✅ {unit.name} 回測完成: "
            f"{result.total_trades} 筆, "
            f"勝率 {result.win_rate:.1%}, "
            f"PnL: {result.total_pnl_twd:+,.0f}"
        )

        return result

    def _execute_exit_actions(
        self,
        exit_actions: list,
        *,
        pm,
        executor: BacktestExecutor,
        result: BacktestResult,
        current_time,
        kbar_open: int,
        kbar_high: int,
        kbar_low: int,
        kbar_close: int,
        is_long: bool,
        pending_entry_price: int | None,
        pending_entry_time,
        pending_direction,
        point_value: float,
    ) -> tuple[float, bool]:
        """Execute exit order actions and record trades.

        Returns (equity_delta, position_fully_closed).
        """
        equity_delta = 0.0
        position_closed = False

        for action in exit_actions:
            exit_reason_str = action.metadata.get("exit_reason", "SL")
            exit_reason = ExitReason(exit_reason_str)
            trigger_price = action.metadata.get("trigger_price")

            sim_price = self._calculate_fill_price(
                exit_reason=exit_reason,
                trigger_price=trigger_price,
                kbar_open=kbar_open,
                kbar_high=kbar_high,
                kbar_low=kbar_low,
                kbar_close=kbar_close,
                is_long=is_long,
            )

            executor.set_market_state(sim_price, current_time)
            fill = executor.execute(action)

            if fill.success and fill.fill_price is not None:
                closing_legs: list[tuple[str, int, int]] = []
                if action.leg_id:
                    leg = next(
                        (lg for lg in pm.position.legs if lg.leg_id == action.leg_id),
                        None,
                    )
                    leg_entry = (
                        leg.entry_price if leg and leg.entry_price
                        else pending_entry_price
                    )
                    closing_legs.append((action.leg_id, leg_entry, action.quantity))
                elif "leg_ids" in action.metadata:
                    for lid in action.metadata["leg_ids"]:
                        leg = next(
                            (lg for lg in pm.position.legs if lg.leg_id == lid),
                            None,
                        )
                        leg_entry = (
                            leg.entry_price if leg and leg.entry_price
                            else pending_entry_price
                        )
                        leg_qty = leg.quantity if leg else action.quantity
                        closing_legs.append((lid, leg_entry, leg_qty))

                if action.leg_id:
                    pm.on_fill(
                        action.leg_id,
                        fill.fill_price,
                        current_time,
                        exit_reason,
                    )
                elif "leg_ids" in action.metadata:
                    for lid in action.metadata["leg_ids"]:
                        pm.on_fill(
                            lid, fill.fill_price, current_time, exit_reason
                        )

                if pending_direction is not None:
                    for _lid, leg_ep, leg_qty in closing_legs:
                        ep = leg_ep if leg_ep is not None else pending_entry_price
                        if ep is None:
                            continue
                        if pending_direction == Action.Buy:
                            pnl_points = float(fill.fill_price - ep)
                        else:
                            pnl_points = float(ep - fill.fill_price)
                        pnl_twd = pnl_points * leg_qty * point_value

                        trade = BacktestTrade(
                            trade_id=str(uuid.uuid4()),
                            symbol=self.config.symbol,
                            action=pending_direction or Action.Buy,
                            entry_time=pending_entry_time or current_time,
                            entry_price=ep,
                            exit_time=current_time,
                            exit_price=fill.fill_price,
                            quantity=leg_qty,
                            exit_reason=exit_reason,
                            pnl_points=pnl_points,
                            pnl_twd=pnl_twd,
                        )
                        result.trades.append(trade)
                        equity_delta += pnl_twd
                        dir_str = "多" if pending_direction == Action.Buy else "空"
                        print(
                            f"📉 平{dir_str}倉: {fill.fill_price} | "
                            f"進場:{ep} | "
                            f"{exit_reason.value} | "
                            f"PnL: {pnl_twd:+.0f}"
                        )

                if not pm.has_position:
                    position_closed = True

        return equity_delta, position_closed

    @staticmethod
    def _calculate_fill_price(
        exit_reason: ExitReason,
        trigger_price: int | None,
        kbar_open: int,
        kbar_high: int,
        kbar_low: int,
        kbar_close: int,
        is_long: bool = True,
    ) -> int:
        """計算模擬成交價格（方向感知）

        做多規則：
        - TP：價格向上穿越觸發價
            - 跳空高開（Open >= TP）→ 用 Open
            - K 棒內穿越 → 用 TP 價格
        - SL / TS：價格向下穿越觸發價
            - 跳空低開（Open <= SL）→ 用 Open
            - K 棒內穿越 → 用 SL/TS 價格

        做空規則（反轉）：
        - TP：價格向下穿越觸發價
            - 跳空低開（Open <= TP）→ 用 Open
            - K 棒內穿越 → 用 TP 價格
        - SL / TS：價格向上穿越觸發價
            - 跳空高開（Open >= SL）→ 用 Open
            - K 棒內穿越 → 用 SL/TS 價格

        - FAST_STOP：使用開盤價（方向無關）
        """
        if exit_reason == ExitReason.FAST_STOP:
            return kbar_open

        if trigger_price is None:
            return kbar_close

        if exit_reason == ExitReason.TAKE_PROFIT:
            if is_long:
                # 做多 TP：價格往上穿越
                if kbar_open >= trigger_price:
                    return kbar_open  # 跳空高開
                return trigger_price
            else:
                # 做空 TP：價格往下穿越
                if kbar_open <= trigger_price:
                    return kbar_open  # 跳空低開
                return trigger_price

        if exit_reason in (ExitReason.STOP_LOSS, ExitReason.TRAILING_STOP):
            if is_long:
                # 做多 SL/TS：價格往下穿越
                if kbar_open <= trigger_price:
                    return kbar_open  # 跳空低開
                return trigger_price
            else:
                # 做空 SL/TS：價格往上穿越
                if kbar_open >= trigger_price:
                    return kbar_open  # 跳空高開
                return trigger_price

        if exit_reason == ExitReason.TIME_EXIT:
            # 時間強制平倉：使用收盤價
            return kbar_close

        # 其他情況用收盤價
        return kbar_close

    def _get_historical_data(self) -> KBarList:
        """取得歷史 K 線數據（使用滾動合約，直接從 API 取）"""
        try:
            print(
                f"📊 資料來源: {self.config.data_symbol}/{self.config.data_sub_symbol} "
                f"(交易商品: {self.config.symbol})"
            )
            kbars = self.market_service.get_futures_kbars_by_date_range(
                symbol=self.config.data_symbol,
                sub_symbol=self.config.data_sub_symbol,
                start_date=self.config.start_date,
                end_date=self.config.end_date,
                timeframe=self.config.timeframe,
            )
            return kbars
        except Exception as e:
            print(f"❌ 取得歷史數據失敗: {e}")
            return KBarList()

    def _get_benchmark_data(self, quantity: int = 1) -> dict:
        """計算 Buy & Hold 基準數據

        Args:
            quantity: 持有口數（與策略的 total_quantity 一致）
        """
        if not self._kbar_list or len(self._kbar_list) < 31:
            return {}

        point_value = get_point_value(self.config.symbol)
        # 從第 30 根 K 棒開始（與策略回測相同起點）
        start_kbar = self._kbar_list[30]
        end_kbar = self._kbar_list[-1]
        entry_price = int(start_kbar.close)
        exit_price = int(end_kbar.close)

        bh_pnl_points = exit_price - entry_price
        bh_pnl_twd = bh_pnl_points * point_value * quantity
        bh_return_pct = bh_pnl_twd / self.config.initial_capital * 100

        # 計算 Buy & Hold 權益曲線
        bh_equity_curve: list[tuple[datetime, float]] = []
        for i in range(30, len(self._kbar_list)):
            kbar = self._kbar_list[i]
            unrealized = (int(kbar.close) - entry_price) * point_value * quantity
            bh_equity_curve.append(
                (kbar.time, self.config.initial_capital + unrealized)
            )

        # 計算 Buy & Hold 最大回撤
        peak = self.config.initial_capital
        max_dd = 0.0
        for _, equity in bh_equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        return {
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "pnl_points": bh_pnl_points,
            "pnl_twd": bh_pnl_twd,
            "return_pct": bh_return_pct,
            "max_drawdown": max_dd,
            "equity_curve": bh_equity_curve,
        }

    def generate_report(self, results: dict[str, BacktestResult]) -> str:
        """產生回測報告（多 TradingUnit 比較 + Buy & Hold 基準）"""
        report = []
        report.append("=" * 70)
        report.append("📊 回測報告")
        report.append("=" * 70)
        report.append(
            f"📅 期間: {self.config.start_date.strftime('%Y-%m-%d')} ~ "
            f"{self.config.end_date.strftime('%Y-%m-%d')}"
        )
        report.append(f"📈 商品: {self.config.symbol} ({self.config.sub_symbol})")
        timeframes = sorted({r.config.timeframe for r in results.values()})
        report.append(f"⏱  時間尺度: {', '.join(timeframes)}")
        report.append(f"💰 初始資金: {self.config.initial_capital:,.0f}")
        report.append("")

        # Buy & Hold 基準（使用策略中最大的口數）
        max_qty = max((r.config.order_quantity for r in results.values()), default=1)
        benchmark = self._get_benchmark_data(quantity=max_qty)
        if benchmark:
            report.append(f"{'─' * 70}")
            report.append(f"📌 基準: Buy & Hold（買入持有 {benchmark['quantity']} 口）")
            report.append(f"{'─' * 70}")
            report.append(
                f"  進場: {benchmark['entry_price']} → 出場: {benchmark['exit_price']}"
            )
            report.append(f"  盈虧 (點): {benchmark['pnl_points']:+,}")
            report.append(f"  盈虧 (TWD): {benchmark['pnl_twd']:+,.0f}")
            report.append(f"  報酬率: {benchmark['return_pct']:+.2f}%")
            report.append(f"  最大回撤: {benchmark['max_drawdown']:.2%}")
            report.append("")

        for unit_name, result in results.items():
            report.append(f"{'─' * 70}")
            report.append(f"🏷  {unit_name}")
            report.append(f"{'─' * 70}")
            report.append(f"  交易次數: {result.total_trades}")
            report.append(f"  勝率: {result.win_rate:.1%}")
            report.append(f"  總盈虧 (點): {result.total_pnl_points:+.0f}")
            report.append(f"  總盈虧 (TWD): {result.total_pnl_twd:+,.0f}")
            strategy_return = result.total_pnl_twd / self.config.initial_capital * 100
            report.append(f"  報酬率: {strategy_return:+.2f}%")
            if result.profit_factor == float("inf"):
                report.append("  盈虧比: ∞")
            else:
                report.append(f"  盈虧比: {result.profit_factor:.2f}")
            report.append(f"  最大回撤: {result.max_drawdown:.2%}")
            report.append(f"  夏普比率: {result.sharpe_ratio:.3f}")

            # 與 Buy & Hold 比較
            if benchmark:
                alpha = strategy_return - benchmark["return_pct"]
                report.append(
                    f"  vs Buy&Hold: {'+' if alpha >= 0 else ''}{alpha:.2f}% "
                    f"({'優於' if alpha > 0 else '劣於'}基準)"
                )
            report.append("")

            # 交易明細
            if result.trades:
                report.append("  📋 交易明細:")
                for j, trade in enumerate(result.trades, 1):
                    dir_str = "多" if trade.action == Action.Buy else "空"
                    report.append(
                        f"    {j:3d}. [{dir_str}] {trade.entry_price} → {trade.exit_price} | "
                        f"{trade.quantity}口 | "
                        f"{trade.exit_reason.value} | {trade.pnl_twd:+,.0f}"
                    )
                report.append("")

        report.append("=" * 70)
        return "\n".join(report)

    def generate_chart(
        self,
        results: dict[str, BacktestResult],
        save_path: str | None = None,
    ) -> str | None:
        """產生權益曲線比較圖（策略 vs Buy & Hold）

        Args:
            results: 回測結果
            save_path: 儲存路徑，None 則自動產生

        Returns:
            儲存的檔案路徑
        """
        try:
            import matplotlib

            matplotlib.use("Agg")  # 無 GUI 後端
            import matplotlib.dates as mdates
            import matplotlib.pyplot as plt
        except ImportError:
            print("⚠️  matplotlib 未安裝，無法產生圖表。請執行: uv add matplotlib")
            return None

        max_qty = max((r.config.order_quantity for r in results.values()), default=1)
        benchmark = self._get_benchmark_data(quantity=max_qty)
        if not benchmark:
            print("⚠️  無基準數據，無法產生圖表")
            return None

        # 設定中文字體
        plt.rcParams["font.sans-serif"] = [
            "Arial Unicode MS",
            "Heiti TC",
            "Microsoft JhengHei",
            "Noto Sans CJK TC",
            "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False

        fig, axes = plt.subplots(2, 1, figsize=(16, 10), height_ratios=[3, 1])
        fig.suptitle(
            f"回測報告: {self.config.symbol} "
            f"({self.config.start_date.strftime('%Y-%m-%d')} ~ "
            f"{self.config.end_date.strftime('%Y-%m-%d')})",
            fontsize=14,
            fontweight="bold",
        )

        ax1 = axes[0]  # 權益曲線
        ax2 = axes[1]  # 指數走勢

        # === 上圖：權益曲線（百分比報酬） ===
        initial = self.config.initial_capital

        # Buy & Hold 曲線
        bh_times = [t for t, _ in benchmark["equity_curve"]]
        bh_returns = [
            ((e - initial) / initial) * 100 for _, e in benchmark["equity_curve"]
        ]
        ax1.plot(
            bh_times,
            bh_returns,
            label=f"Buy & Hold ({benchmark['quantity']}口)",
            color="gray",
            linewidth=1.5,
            linestyle="--",
            alpha=0.7,
        )

        # 策略曲線
        colors = ["#2196F3", "#FF5722", "#4CAF50", "#9C27B0", "#FF9800"]
        for idx, (unit_name, result) in enumerate(results.items()):
            if not result.equity_curve:
                continue
            times = [t for t, _ in result.equity_curve]
            returns = [((e - initial) / initial) * 100 for _, e in result.equity_curve]
            color = colors[idx % len(colors)]
            ax1.plot(times, returns, label=unit_name, color=color, linewidth=1.5)

        ax1.set_ylabel("累積報酬率 (%)")
        ax1.legend(loc="upper left", fontsize=9)
        ax1.grid(True, alpha=0.3)
        ax1.axhline(y=0, color="black", linewidth=0.5)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

        # === 下圖：指數走勢 ===
        if self._kbar_list and len(self._kbar_list) > 30:
            idx_times = [
                self._kbar_list[i].time for i in range(30, len(self._kbar_list))
            ]
            idx_prices = [
                int(self._kbar_list[i].close) for i in range(30, len(self._kbar_list))
            ]
            ax2.plot(idx_times, idx_prices, color="#333333", linewidth=1)
            ax2.fill_between(idx_times, idx_prices, alpha=0.1, color="#333333")
            ax2.set_ylabel(f"{self.config.symbol} 指數")
            ax2.grid(True, alpha=0.3)
            ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
            ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

        plt.tight_layout()

        # 儲存
        if save_path is None:
            chart_dir = "data/backtest"
            os.makedirs(chart_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = f"{chart_dir}/chart_{self.config.symbol}_{timestamp}.png"

        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"📈 圖表已儲存: {save_path}")
        return save_path

    def save_report(
        self,
        results: dict[str, BacktestResult],
        filename: str | None = None,
    ) -> str:
        """儲存回測報告"""
        backtest_dir = "data/backtest"
        os.makedirs(backtest_dir, exist_ok=True)

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{backtest_dir}/backtest_{self.config.symbol}_{timestamp}.txt"

        report = self.generate_report(results)
        with open(filename, "w", encoding="utf-8") as f:
            f.write(report)

        print(f"💾 報告已儲存: {filename}")
        return filename
