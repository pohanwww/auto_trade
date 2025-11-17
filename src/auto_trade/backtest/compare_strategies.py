#!/usr/bin/env python3
"""比較不同策略的回測結果"""

from datetime import datetime, timedelta

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.models.backtest import BacktestConfig
from auto_trade.services.backtest_service import BacktestService
from auto_trade.services.market_service import MarketService
from auto_trade.services.strategy_service import StrategyService


def run_comparison():
    """執行策略比較"""
    print("=" * 80)
    print("🔬 策略比較回測工具")
    print("=" * 80)
    print()

    # 回測設定
    symbol = "MXF"
    sub_symbol = "MXF202511"
    days = 90
    capital = 1000000

    try:
        # 載入配置
        config = Config()

        # 建立API客戶端
        api_client = create_api_client(
            config.api_key,
            config.secret_key,
            config.ca_cert_path,
            config.ca_password,
            simulation=True,
        )

        # 建立服務
        market_service = MarketService(api_client)
        strategy_service = StrategyService()
        backtest_service = BacktestService(market_service, strategy_service)

        # 設定回測參數
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # ===== 策略 1: 原始策略 =====
        print("📊 策略 1: 原始策略（無 MACD 快速停損）")
        print("-" * 80)

        config1 = BacktestConfig(
            symbol=symbol,
            sub_symbol=sub_symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=capital,
            order_quantity=2,
            stop_loss_points=80,
            start_trailing_stop_points=250,
            trailing_stop_points=250,
            trailing_stop_points_rate=0.0095,
            take_profit_points=600,
            take_profit_points_rate=0.02,
            timeframe="30m",
            max_positions=1,
            enable_trailing_stop=True,
            enable_take_profit=True,
            enable_macd_fast_stop=False,  # 不啟用快速停損
        )

        print(
            f"期間: {start_date.strftime('%Y-%m-%d')} - {end_date.strftime('%Y-%m-%d')}"
        )
        print(f"初始資金: {capital:,.0f}")
        print()

        result1 = backtest_service.run_backtest(config1)
        print()

        # ===== 策略 2: MACD 快速停損策略 =====
        print("=" * 80)
        print("📊 策略 2: MACD 快速停損策略")
        print("-" * 80)

        config2 = BacktestConfig(
            symbol=symbol,
            sub_symbol=sub_symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=capital,
            order_quantity=2,
            stop_loss_points=80,
            start_trailing_stop_points=250,
            trailing_stop_points=250,
            trailing_stop_points_rate=0.0095,
            take_profit_points=600,
            take_profit_points_rate=0.02,
            timeframe="30m",
            max_positions=1,
            enable_trailing_stop=True,
            enable_take_profit=True,
            enable_macd_fast_stop=True,  # 啟用快速停損
            min_acceleration_threshold=0.0,  # 無過濾（所有死叉）
        )

        print("MACD 快速停損: 啟用（無過濾，所有死叉）")
        print()

        result2 = backtest_service.run_backtest(config2)
        print()

        # ===== 策略 3: MACD 快速停損策略（強死叉） =====
        print("=" * 80)
        print("📊 策略 3: MACD 快速停損策略（強死叉 ≥ 3.0）")
        print("-" * 80)

        config3 = BacktestConfig(
            symbol=symbol,
            sub_symbol=sub_symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=capital,
            order_quantity=2,
            stop_loss_points=80,
            start_trailing_stop_points=250,
            trailing_stop_points=250,
            trailing_stop_points_rate=0.0095,
            take_profit_points=600,
            take_profit_points_rate=0.02,
            timeframe="30m",
            max_positions=1,
            enable_trailing_stop=True,
            enable_take_profit=True,
            enable_macd_fast_stop=True,  # 啟用快速停損
            min_acceleration_threshold=3.0,  # 強死叉過濾
        )

        print("MACD 快速停損: 啟用（強死叉，加速度 ≥ 3.0）")
        print()

        result3 = backtest_service.run_backtest(config3)
        print()

        # ===== 生成比較報告 =====
        print("=" * 80)
        print("📈 三策略比較結果（90天）")
        print("=" * 80)
        print()

        # 計算統計
        result1.calculate_statistics()
        result2.calculate_statistics()
        result3.calculate_statistics()

        # 比較表格
        print(f"{'指標':<20} {'原始策略':<20} {'無過濾FS':<20} {'強死叉FS':<20}")
        print("-" * 85)

        # 基本統計
        print(
            f"{'交易次數':<20} {result1.total_trades:<20} {result2.total_trades:<20} {result3.total_trades:<20}"
        )
        print(
            f"{'勝率':<20} {result1.win_rate:<19.2f}% {result2.win_rate:<19.2f}% {result3.win_rate:<19.2f}%"
        )
        print(
            f"{'獲利次數':<20} {result1.winning_trades:<20} {result2.winning_trades:<20} {result3.winning_trades:<20}"
        )
        print(
            f"{'虧損次數':<20} {result1.losing_trades:<20} {result2.losing_trades:<20} {result3.losing_trades:<20}"
        )

        print()

        # 盈虧統計
        print(
            f"{'總盈虧 (TWD)':<20} {result1.total_pnl_twd:<19,.0f} {result2.total_pnl_twd:<19,.0f} {result3.total_pnl_twd:<19,.0f}"
        )
        print(
            f"{'總獲利 (TWD)':<20} {result1.gross_profit:<19,.0f} {result2.gross_profit:<19,.0f} {result3.gross_profit:<19,.0f}"
        )
        print(
            f"{'總虧損 (TWD)':<20} {result1.gross_loss:<19,.0f} {result2.gross_loss:<19,.0f} {result3.gross_loss:<19,.0f}"
        )
        print(
            f"{'盈虧比':<20} {result1.profit_factor:<19.2f} {result2.profit_factor:<19.2f} {result3.profit_factor:<19.2f}"
        )

        print()

        # 風險指標
        print(
            f"{'最大回撤 (%)':<20} {result1.max_drawdown:<19.2f} {result2.max_drawdown:<19.2f} {result3.max_drawdown:<19.2f}"
        )
        print(
            f"{'夏普比率':<20} {result1.sharpe_ratio:<19.2f} {result2.sharpe_ratio:<19.2f} {result3.sharpe_ratio:<19.2f}"
        )
        print(
            f"{'持倉時間(小時)':<20} {result1.avg_trade_duration_hours:<19.1f} {result2.avg_trade_duration_hours:<19.1f} {result3.avg_trade_duration_hours:<19.1f}"
        )

        print()
        print("=" * 85)

        # 相對於原始策略的改善
        print()
        print("📊 相對於原始策略的改善:")
        print()

        # 無過濾 vs 原始
        pnl_diff_2 = result2.total_pnl_twd - result1.total_pnl_twd
        pnl_pct_2 = (
            (pnl_diff_2 / result1.total_pnl_twd * 100)
            if result1.total_pnl_twd != 0
            else 0
        )
        loss_diff_2 = result2.gross_loss - result1.gross_loss
        loss_pct_2 = (
            (loss_diff_2 / result1.gross_loss * 100) if result1.gross_loss != 0 else 0
        )
        dd_diff_2 = result2.max_drawdown - result1.max_drawdown

        print("📌 無過濾快速停損 vs 原始策略:")
        print(f"   總盈虧：{pnl_diff_2:+,.0f} TWD ({pnl_pct_2:+.2f}%)")
        print(f"   總虧損：{loss_diff_2:+,.0f} TWD ({loss_pct_2:+.2f}%)")
        print(f"   最大回撤：{dd_diff_2:+.2f}%")
        print(f"   盈虧比：{result2.profit_factor - result1.profit_factor:+.2f}")

        # 強死叉 vs 原始
        pnl_diff_3 = result3.total_pnl_twd - result1.total_pnl_twd
        pnl_pct_3 = (
            (pnl_diff_3 / result1.total_pnl_twd * 100)
            if result1.total_pnl_twd != 0
            else 0
        )
        loss_diff_3 = result3.gross_loss - result1.gross_loss
        loss_pct_3 = (
            (loss_diff_3 / result1.gross_loss * 100) if result1.gross_loss != 0 else 0
        )
        dd_diff_3 = result3.max_drawdown - result1.max_drawdown

        print()
        print("📌 強死叉快速停損 vs 原始策略:")
        print(f"   總盈虧：{pnl_diff_3:+,.0f} TWD ({pnl_pct_3:+.2f}%)")
        print(f"   總虧損：{loss_diff_3:+,.0f} TWD ({loss_pct_3:+.2f}%)")
        print(f"   最大回撤：{dd_diff_3:+.2f}%")
        print(f"   盈虧比：{result3.profit_factor - result1.profit_factor:+.2f}")

        # FS效果分析
        print()
        print("=" * 85)
        print("⚡ 快速停損（FS）效果分析:")
        print()

        # 策略 2
        fs_count2 = sum(
            1 for trade in result2.trades if trade.exit_reason.value == "FS"
        )
        sl_count2 = sum(
            1 for trade in result2.trades if trade.exit_reason.value == "SL"
        )
        print("無過濾FS:")
        print(f"   FS 次數: {fs_count2}")
        if fs_count2 > 0:
            fs_pnl2 = sum(
                trade.pnl_twd
                for trade in result2.trades
                if trade.exit_reason.value == "FS"
            )
            print(f"   FS 總盈虧: {fs_pnl2:,.0f} TWD")
            print(f"   FS 平均虧損: {fs_pnl2 / fs_count2:,.0f} TWD")
        print(f"   SL 次數: {sl_count2}")

        # 策略 3
        fs_count3 = sum(
            1 for trade in result3.trades if trade.exit_reason.value == "FS"
        )
        sl_count3 = sum(
            1 for trade in result3.trades if trade.exit_reason.value == "SL"
        )
        print()
        print("強死叉FS (≥3.0):")
        print(f"   FS 次數: {fs_count3}")
        if fs_count3 > 0:
            fs_pnl3 = sum(
                trade.pnl_twd
                for trade in result3.trades
                if trade.exit_reason.value == "FS"
            )
            print(f"   FS 總盈虧: {fs_pnl3:,.0f} TWD")
            print(f"   FS 平均虧損: {fs_pnl3 / fs_count3:,.0f} TWD")
        print(f"   SL 次數: {sl_count3}")

        print()
        print("=" * 85)

        # 結論
        print()
        print("🏆 結論:")
        print()

        # 找出最佳策略
        results_list = [
            ("原始策略", result1.total_pnl_twd),
            ("無過濾快速停損", result2.total_pnl_twd),
            ("強死叉快速停損", result3.total_pnl_twd),
        ]
        best_strategy = max(results_list, key=lambda x: x[1])

        print(f"✨ 總盈虧最高：{best_strategy[0]} ({best_strategy[1]:,.0f} TWD)")

        # 風險控制最佳
        dd_results = [
            ("原始策略", result1.max_drawdown),
            ("無過濾快速停損", result2.max_drawdown),
            ("強死叉快速停損", result3.max_drawdown),
        ]
        best_dd = min(dd_results, key=lambda x: x[1])
        print(f"✨ 風險控制最佳：{best_dd[0]} (回撤 {best_dd[1]:.2f}%)")

        # 盈虧比最高
        pf_results = [
            ("原始策略", result1.profit_factor),
            ("無過濾快速停損", result2.profit_factor),
            ("強死叉快速停損", result3.profit_factor),
        ]
        best_pf = max(pf_results, key=lambda x: x[1])
        print(f"✨ 盈虧比最高：{best_pf[0]} ({best_pf[1]:.2f})")

        print()

        if result2.max_drawdown < result1.max_drawdown:
            print(
                f"✅ 快速停損減少了最大回撤: {result1.max_drawdown - result2.max_drawdown:,.0f} TWD"
            )

        print()
        print("=" * 80)

        # 保存詳細報告
        print()
        print("💾 保存詳細報告...")
        file1 = backtest_service.save_results(result1, suffix="_original")
        file2 = backtest_service.save_results(result2, suffix="_fast_stop")

        print(f"✅ 原始策略報告: {file1}")
        print(f"✅ 快速停損策略報告: {file2}")
        print()

    except Exception as e:
        print(f"❌ 回測失敗: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(run_comparison())
