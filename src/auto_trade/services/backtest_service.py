"""回測服務 - 整合所有回測功能"""

import os
import uuid
from datetime import datetime

from auto_trade.models import Action, ExitReason, KBarList, TradingSignal
from auto_trade.models.backtest import (
    BacktestConfig,
    BacktestPosition,
    BacktestResult,
    BacktestTrade,
)
from auto_trade.services.market_service import MarketService
from auto_trade.services.strategy_service import StrategyService


class BacktestService:
    """回測服務 - 整合所有回測功能"""

    def __init__(
        self, market_service: MarketService, strategy_service: StrategyService
    ):
        self.market_service = market_service
        self.strategy_service = strategy_service

    def run_backtest(self, config: BacktestConfig) -> BacktestResult:
        """執行回測"""
        print(f"🚀 開始回測: {config.symbol} ({config.start_date} - {config.end_date})")

        # 初始化回測結果
        result = BacktestResult(config=config)
        result.equity_curve.append((config.start_date, config.initial_capital))

        # 獲取歷史數據
        kbars = self._get_historical_data(config)
        if not kbars:
            print("❌ 無法獲取歷史數據")
            return result

        print(f"📊 獲取到 {len(kbars)} 根K線數據")

        # 如果啟用 MACD 快速停損，計算 MACD 指標
        macd_list = None
        if config.enable_macd_fast_stop:
            macd_list = self.strategy_service.calculate_macd(
                kbars,
                config.macd_fast_period,
                config.macd_slow_period,
                config.macd_signal_period,
            )
            print("📈 計算 MACD 指標完成")

        # 初始化狀態
        current_position: BacktestPosition | None = None
        current_equity = config.initial_capital
        trade_counter = 0
        is_in_macd_death_cross = False  # 記錄是否處於MACD死叉狀態（持續追蹤）

        # 按時間順序處理每根K線
        for i, kbar in enumerate(kbars):
            current_time = kbar.time
            current_price = kbar.close
            current_high = kbar.high
            current_low = kbar.low

            # 回測不限制交易時間，處理所有數據

            # 更新權益曲線
            result.equity_curve.append((current_time, current_equity))

            # 檢查持倉狀態
            if current_position:
                # 檢查是否需要平倉
                exit_reason, exit_price_override = self._check_exit_conditions(
                    current_position,
                    kbar,
                    current_high,
                    current_low,
                    config,
                    macd_list,
                    i,
                    is_in_macd_death_cross,
                )

                # 如果平倉，重置死叉狀態
                if exit_reason:
                    is_in_macd_death_cross = False

                if exit_reason:
                    # 執行平倉
                    trade = self._close_position(
                        current_position,
                        current_time,
                        current_high,
                        current_low,
                        exit_reason,
                        config,
                        exit_price_override,
                    )
                    result.trades.append(trade)

                    # 更新權益
                    current_equity += trade.pnl_twd

                    # 清除持倉
                    current_position = None
                    print(
                        f"📉 平倉: {trade.action.value} @ {trade.exit_price:.1f}, 盈虧: {trade.pnl_twd:.0f}"
                    )
                else:
                    # 持續追蹤 MACD 死叉狀態
                    if (
                        config.enable_macd_fast_stop
                        and not current_position.trailing_stop_active
                        and macd_list is not None
                        and i >= 1
                    ):
                        current_macd = macd_list[i]
                        previous_macd = macd_list[i - 1]

                        if current_position.action == Action.Buy:
                            # 檢測死叉（進入死叉狀態）
                            if (
                                not is_in_macd_death_cross
                                and previous_macd.macd_line >= previous_macd.signal_line
                                and current_macd.macd_line < current_macd.signal_line
                            ):
                                # 檢查死叉強度（只有強死叉才進入監控）
                                death_cross_strength = abs(
                                    current_macd.macd_line - current_macd.signal_line
                                )
                                if death_cross_strength > 3.0:
                                    is_in_macd_death_cross = True
                                    print(
                                        f"🔴 強死叉確認（強度 {death_cross_strength:.2f}）- MACD:{current_macd.macd_line:.1f} < Signal:{current_macd.signal_line:.1f}，持續監控快速停損"
                                    )
                                else:
                                    print(
                                        f"⚪ 弱死叉（強度 {death_cross_strength:.2f} <= 5.0）- MACD:{current_macd.macd_line:.1f} < Signal:{current_macd.signal_line:.1f}，忽略"
                                    )

                            # 檢測金叉（解除死叉狀態）
                            elif (
                                is_in_macd_death_cross
                                and previous_macd.macd_line <= previous_macd.signal_line
                                and current_macd.macd_line > current_macd.signal_line
                            ):
                                is_in_macd_death_cross = False
                                print(
                                    f"✅ MACD 金叉，解除死叉狀態 (MACD:{current_macd.macd_line:.1f} > Signal:{current_macd.signal_line:.1f})"
                                )

                    # 繼續更新移動停損等
                    # 更新移動停損 (使用高點)
                    if (
                        config.enable_trailing_stop
                        and current_position.trailing_stop_active
                    ):
                        trailing_stop_points = config.calculate_trailing_stop_points(
                            current_position.entry_price
                        )
                        if current_position.action == Action.Buy:
                            current_position.update_trailing_stop(
                                current_high, trailing_stop_points
                            )
                        else:
                            current_position.update_trailing_stop(
                                current_low, trailing_stop_points
                            )

                    # 更新最大獲利/虧損 (使用高點和低點)
                    if current_position.action == Action.Buy:
                        profit_points = current_high - current_position.entry_price
                    else:
                        profit_points = current_position.entry_price - current_low

                    current_position.max_profit_points = max(
                        current_position.max_profit_points, profit_points
                    )
                    current_position.max_loss_points = min(
                        current_position.max_loss_points, profit_points
                    )

            # 檢查開倉信號
            if not current_position and len(kbars) > i + 30:  # 確保有足夠數據計算MACD
                # 創建包含到當前時間的 KBarList
                current_kbars = KBarList(
                    kbars=kbars.kbars[: i + 1],
                    symbol=kbars.symbol,
                    timeframe=kbars.timeframe,
                )
                signal = self._generate_signal(current_kbars, current_price, config)

                if signal.action != Action.Hold:
                    # 執行開倉
                    current_position = self._open_position(
                        signal, current_time, kbar.open, config, kbars
                    )
                    trade_counter += 1
                    print(f"📈 開倉: {signal.action.value} @ {kbar.open:.1f}")

        # 計算統計指標
        result.calculate_statistics()

        # 計算回測期間
        result.backtest_duration_days = (config.end_date - config.start_date).days

        print(
            f"✅ 回測完成: {result.total_trades} 筆交易, 總盈虧: {result.total_pnl_twd:.0f}"
        )

        return result

    def _get_historical_data(self, config: BacktestConfig) -> KBarList:
        """獲取歷史數據"""
        try:
            # 計算需要多少天的數據
            days_diff = (config.end_date - config.start_date).days + 1

            # 直接獲取指定時間尺度的K線數據
            kbars = self.market_service.get_futures_kbars_with_timeframe(
                symbol=config.symbol,
                sub_symbol=config.sub_symbol,
                timeframe=config.timeframe,
                days=days_diff,
            )

            return kbars
        except Exception as e:
            print(f"❌ 獲取歷史數據失敗: {e}")
            return KBarList()

    def _is_trading_time(self, time: datetime) -> bool:
        """檢查是否為交易時間"""
        # 台灣期貨交易時間
        hour = time.hour
        minute = time.minute

        # 早上: 08:45-13:45
        if 8 <= hour < 13 or (hour == 13 and minute <= 45):
            return True

        return hour >= 15 or hour < 5

    def _generate_signal(
        self, kbars: KBarList, current_price: float, config: BacktestConfig
    ) -> TradingSignal:
        """生成交易信號"""
        try:
            # 直接使用 KBarList 計算 MACD
            macd_list = self.strategy_service.calculate_macd(kbars)

            # 取得最新的MACD值
            latest_macd = macd_list.get_latest(3)  # 取得最新3個數據點
            if len(latest_macd) < 2:
                return TradingSignal(
                    action=Action.Hold,
                    symbol=config.symbol,
                    price=current_price,
                    reason="Insufficient MACD data",
                )

            current_macd = latest_macd[-2]
            previous_macd = latest_macd[-3]

            print(f"latest_macd: {latest_macd[-1].macd_line:.1f}")
            print(f"latest_signal: {latest_macd[-1].signal_line:.1f}")
            current_signal = current_macd.signal_line
            previous_signal = previous_macd.signal_line

            # MACD金叉策略：MACD < 30 且金叉時買入
            if (
                (current_macd.macd_line + current_macd.signal_line) / 2 < 30
                and previous_macd.macd_line <= previous_signal
                and current_macd.macd_line > current_signal
            ):
                return TradingSignal(
                    action=Action.Buy,
                    symbol=config.symbol,
                    price=current_price,
                    confidence=0.8,
                    reason=f"MACD Golden Cross: MACD({current_macd.macd_line:.2f}) > Signal({current_signal:.2f})",
                    timestamp=datetime.now(),
                )

            return TradingSignal(
                action=Action.Hold,
                symbol=config.symbol,
                price=current_price,
                reason="No signal",
                timestamp=datetime.now(),
            )

        except Exception as e:
            print(f"❌ 生成信號失敗: {e}")
            return TradingSignal(
                action=Action.Hold,
                symbol=config.symbol,
                price=current_price,
                reason=f"Signal generation error: {e}",
            )

    def _open_position(
        self,
        signal: TradingSignal,
        time: datetime,
        price: float,
        config: BacktestConfig,
        kbars: KBarList,
    ) -> BacktestPosition:
        """開倉"""
        # 計算停損價格 - 使用前30根KBar的最低點減80點（與實際交易一致）
        stop_loss_price = self._calculate_stop_loss_from_kbars(
            signal.action, time, kbars, config.stop_loss_points
        )

        # 計算獲利價格
        take_profit_points = config.calculate_take_profit_points(price)
        if signal.action == Action.Buy:
            take_profit_price = price + take_profit_points
        else:  # Sell
            take_profit_price = price - take_profit_points

        position = BacktestPosition(
            symbol=config.symbol,
            action=signal.action,
            entry_time=time,
            entry_price=price,
            quantity=config.order_quantity,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
        )

        return position

    def _calculate_stop_loss_from_kbars(
        self,
        action: Action,
        entry_time: datetime,
        kbars: KBarList,
        stop_loss_points: int,
    ) -> float:
        """根據前30根KBar計算停損價格（與實際交易邏輯一致）"""
        # 找到進場前的KBar
        pre_entry_kbars = [kbar for kbar in kbars if kbar.time <= entry_time]

        if len(pre_entry_kbars) < 30:
            # 如果歷史數據不足30根，使用進場價格計算（fallback）
            print(
                f"⚠️ 歷史KBar不足30根 ({len(pre_entry_kbars)}根)，使用進場價格計算停損"
            )
            # 找到當前KBar的價格
            current_kbar = next(
                (kbar for kbar in kbars if kbar.time == entry_time), None
            )
            if current_kbar:
                current_price = current_kbar.close
                if action == Action.Buy:
                    return current_price - stop_loss_points
                else:
                    return current_price + stop_loss_points
            else:
                # 如果找不到當前KBar，使用最後一根KBar
                if pre_entry_kbars:
                    last_price = pre_entry_kbars[-1].close
                    if action == Action.Buy:
                        return last_price - stop_loss_points
                    else:
                        return last_price + stop_loss_points
                else:
                    raise ValueError("無法計算停損價格：沒有可用的KBar數據")

        # 取前30根KBar的最低點
        recent_kbars = pre_entry_kbars[-30:]
        min_price = min(kbar.low for kbar in recent_kbars)

        # 計算停損價格
        if action == Action.Buy:
            stop_loss_price = min_price - stop_loss_points
        else:  # Sell
            stop_loss_price = min_price + stop_loss_points

        print(
            f"📊 停損計算: 前30根最低點 {min_price:.1f} ± {stop_loss_points} = {stop_loss_price:.1f}"
        )

        return stop_loss_price

    def _check_exit_conditions(
        self,
        position: BacktestPosition,
        current_kbar,
        current_high: float,
        current_low: float,
        config: BacktestConfig,
        macd_list=None,
        current_index: int = 0,
        is_in_macd_death_cross: bool = False,
    ) -> tuple[ExitReason | None, float | None]:
        """檢查平倉條件

        Returns:
            (exit_reason, exit_price_override): 平倉原因和可選的覆蓋出場價格
        """
        # 檢查 MACD 快速停損（處於死叉狀態時持續檢查）
        if (
            config.enable_macd_fast_stop
            and not position.trailing_stop_active
            and is_in_macd_death_cross
        ):
            # 使用開盤價檢查
            open_price = current_kbar.open

            # 計算虧損
            if position.action == Action.Buy:
                loss_points = position.entry_price - open_price

                # 使用 stop_loss_points 作為門檻（與實際交易一致）
                if loss_points > config.stop_loss_points:
                    print(
                        f"⚡ MACD 快速停損觸發: 開盤價 {open_price:.1f}, 虧損 {loss_points:.1f} 點 >= 門檻 {config.stop_loss_points} 點 (處於死叉狀態)"
                    )
                    return ExitReason.FAST_STOP, open_price  # 使用開盤價作為出場價

        # 檢查獲利了結 (使用高點檢查)
        if config.enable_take_profit and (
            (
                position.action == Action.Buy
                and current_high >= position.take_profit_price
            )
            or (
                position.action == Action.Sell
                and current_low <= position.take_profit_price
            )
        ):
            return ExitReason.TAKE_PROFIT, None

        # 檢查移動停損 (使用低點檢查，優先於一般停損)
        if (
            config.enable_trailing_stop
            and position.trailing_stop_price
            and (
                (
                    position.action == Action.Buy
                    and current_low <= position.trailing_stop_price
                )
                or (
                    position.action == Action.Sell
                    and current_high >= position.trailing_stop_price
                )
            )
        ):
            return ExitReason.TRAILING_STOP, None

        # 檢查一般停損 (使用低點檢查)
        if (
            position.action == Action.Buy and current_low <= position.stop_loss_price
        ) or (
            position.action == Action.Sell and current_high >= position.stop_loss_price
        ):
            return ExitReason.STOP_LOSS, None

        # 檢查是否啟動移動停損 (使用高點檢查)
        if config.enable_trailing_stop and not position.trailing_stop_active:
            profit_points = 0
            if position.action == Action.Buy:
                profit_points = current_high - position.entry_price
            else:
                profit_points = position.entry_price - current_low

            if profit_points >= config.start_trailing_stop_points:
                position.trailing_stop_active = True
                trailing_stop_points = config.calculate_trailing_stop_points(
                    position.entry_price
                )
                if position.action == Action.Buy:
                    position.update_trailing_stop(current_high, trailing_stop_points)
                else:
                    position.update_trailing_stop(current_low, trailing_stop_points)

        return None, None

    def _close_position(
        self,
        position: BacktestPosition,
        time: datetime,
        current_high: float,
        current_low: float,
        exit_reason: ExitReason,
        config: BacktestConfig,  # noqa: ARG002
        exit_price_override: float | None = None,
    ) -> BacktestTrade:
        """平倉"""
        # 如果有覆蓋價格（例如快速停損使用開盤價），優先使用
        if exit_price_override is not None:
            exit_price = exit_price_override
        # 否則根據出場原因決定實際成交價格
        elif exit_reason == ExitReason.TAKE_PROFIT:
            # 獲利了結：使用目標價格
            exit_price = position.take_profit_price
        elif exit_reason == ExitReason.TRAILING_STOP:
            # 移動停損：使用移動停損價格
            exit_price = position.trailing_stop_price
        elif exit_reason == ExitReason.STOP_LOSS:
            # 一般停損：使用停損價格
            exit_price = position.stop_loss_price
        else:
            # 其他情況：使用收盤價
            exit_price = (current_high + current_low) / 2

        # 創建交易記錄
        trade = BacktestTrade(
            trade_id=str(uuid.uuid4()),
            symbol=position.symbol,
            action=position.action,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=time,
            exit_price=exit_price,
            quantity=position.quantity,
            exit_reason=exit_reason,
        )

        # 計算盈虧
        trade.calculate_pnl()

        return trade

    def generate_report(self, result: BacktestResult) -> str:
        """生成回測報告"""
        report = []
        report.append("=" * 60)
        report.append("📊 回測報告")
        report.append("=" * 60)

        # 基本資訊
        report.append(f"📈 商品: {result.config.symbol} ({result.config.sub_symbol})")
        report.append(
            f"📅 回測期間: {result.config.start_date.strftime('%Y-%m-%d')} - {result.config.end_date.strftime('%Y-%m-%d')}"
        )
        report.append(f"💰 初始資金: {result.config.initial_capital:,.0f}")
        report.append(f"⏱️  回測天數: {result.backtest_duration_days} 天")
        report.append("")

        # 策略配置
        report.append("⚙️  策略配置")
        report.append("-" * 30)
        report.append(f"下單數量: {result.config.order_quantity}")
        report.append(f"K線時間尺度: {result.config.timeframe}")
        report.append(f"初始停損點數: {result.config.stop_loss_points}")
        report.append(f"啟動移動停損點數: {result.config.start_trailing_stop_points}")

        # 移動停損顯示（優先顯示百分比）
        if result.config.trailing_stop_points_rate is not None:
            report.append(
                f"移動停損: {result.config.trailing_stop_points_rate * 100}% (進入價格 × {result.config.trailing_stop_points_rate})"
            )
        else:
            report.append(f"移動停損點數: {result.config.trailing_stop_points}")

        # 獲利了結顯示（優先顯示百分比）
        if result.config.take_profit_points_rate is not None:
            report.append(
                f"獲利了結: {result.config.take_profit_points_rate * 100}% (進入價格 × {result.config.take_profit_points_rate})"
            )
        else:
            report.append(f"獲利了結點數: {result.config.take_profit_points}")
        report.append(f"最大同時持倉數: {result.config.max_positions}")
        report.append(
            f"啟用移動停損: {'是' if result.config.enable_trailing_stop else '否'}"
        )
        report.append(
            f"啟用獲利了結: {'是' if result.config.enable_take_profit else '否'}"
        )
        report.append("")

        # MACD 參數
        report.append("📈 MACD 參數")
        report.append("-" * 30)
        report.append(f"快速週期: {result.config.macd_fast_period}")
        report.append(f"慢速週期: {result.config.macd_slow_period}")
        report.append(f"信號週期: {result.config.macd_signal_period}")
        report.append("")

        # 交易統計
        report.append("📊 交易統計")
        report.append("-" * 30)
        report.append(f"總交易次數: {result.total_trades}")
        report.append(f"獲利交易: {result.winning_trades}")
        report.append(f"虧損交易: {result.losing_trades}")
        report.append(f"勝率: {result.win_rate:.2%}")
        report.append("")

        # 盈虧統計
        report.append("💰 盈虧統計")
        report.append("-" * 30)
        report.append(f"總盈虧 (點數): {result.total_pnl_points:.1f}")
        report.append(f"總盈虧 (新台幣): {result.total_pnl_twd:,.0f}")
        report.append(f"總獲利: {result.gross_profit:,.0f}")
        report.append(f"總虧損: {result.gross_loss:,.0f}")
        report.append(f"淨盈虧: {result.total_pnl_twd:,.0f}")
        report.append("")

        # 風險指標
        report.append("⚠️  風險指標")
        report.append("-" * 30)
        report.append(f"最大回撤: {result.max_drawdown:.2%}")

        # 根據時間尺度顯示最大回撤持續時間
        timeframe = result.config.timeframe
        if timeframe.endswith("m"):
            minutes = int(timeframe[:-1])
            duration_hours = result.max_drawdown_duration * minutes / 60
            if duration_hours >= 24:
                duration_days = duration_hours / 24
                report.append(f"最大回撤持續時間: {duration_days:.1f} 天")
            else:
                report.append(f"最大回撤持續時間: {duration_hours:.1f} 小時")
        elif timeframe.endswith("h"):
            hours = int(timeframe[:-1])
            duration_hours = result.max_drawdown_duration * hours
            if duration_hours >= 24:
                duration_days = duration_hours / 24
                report.append(f"最大回撤持續時間: {duration_days:.1f} 天")
            else:
                report.append(f"最大回撤持續時間: {duration_hours:.1f} 小時")
        else:
            report.append(
                f"最大回撤持續時間: {result.max_drawdown_duration} {timeframe}"
            )

        report.append(f"夏普比率: {result.sharpe_ratio:.3f}")
        report.append(f"卡爾瑪比率: {result.calmar_ratio:.3f}")

        # 顯示盈虧比
        if result.profit_factor == float("inf"):
            report.append("盈虧比: ∞ (無虧損交易)")
        else:
            report.append(f"盈虧比: {result.profit_factor:.2f}")

        report.append("")

        # 時間統計
        report.append("⏰ 時間統計")
        report.append("-" * 30)
        report.append(f"平均持倉時間: {result.avg_trade_duration_hours:.1f} 小時")
        report.append("")

        # 交易明細
        if result.trades:
            report.append("📋 交易明細")
            report.append("-" * 30)
            for i, trade in enumerate(result.trades, 1):
                report.append(
                    f"{i:2d}. {trade.action.value} {trade.entry_price:.1f} → {trade.exit_price:.1f} | {trade.exit_reason.value} | {trade.pnl_twd:+.0f}"
                )

        report.append("=" * 60)

        return "\n".join(report)

    def save_results(
        self, result: BacktestResult, filename: str = None, suffix: str = ""
    ) -> str:
        """保存回測結果到檔案"""

        # 確保 data/backtest/ 目錄存在（相對於當前工作目錄）
        backtest_dir = "data/backtest"
        os.makedirs(backtest_dir, exist_ok=True)

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            symbol = result.config.symbol
            filename = (
                f"{backtest_dir}/backtest_results_{symbol}_{timestamp}{suffix}.txt"
            )

        report = self.generate_report(result)

        with open(filename, "w", encoding="utf-8") as f:
            f.write(report)

        print(f"💾 回測結果已保存到: {filename}")
        return filename
