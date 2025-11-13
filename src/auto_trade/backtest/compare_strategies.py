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
            enable_macd_fast_stop=True,  # 啟用快速停損（使用 stop_loss_points 作為門檻）
        )

        print(f"MACD 快速停損: 啟用（虧損 > {config2.stop_loss_points} 點時檢查死叉）")
        print()

        result2 = backtest_service.run_backtest(config2)
        print()

        # ===== 生成比較報告 =====
        print("=" * 80)
        print("📈 策略比較結果")
        print("=" * 80)
        print()

        # 計算統計
        result1.calculate_statistics()
        result2.calculate_statistics()

        # 比較表格
        print(f"{'指標':<25} {'原始策略':<20} {'快速停損策略':<20} {'差異':<15}")
        print("-" * 80)

        # 基本統計
        print(
            f"{'交易次數':<25} {result1.total_trades:<20} {result2.total_trades:<20} {result2.total_trades - result1.total_trades:+<15}"
        )
        print(
            f"{'勝率':<25} {result1.win_rate * 100:<19.2f}% {result2.win_rate * 100:<19.2f}% {(result2.win_rate - result1.win_rate) * 100:+.2f}%"
        )
        print(
            f"{'獲利次數':<25} {result1.winning_trades:<20} {result2.winning_trades:<20} {result2.winning_trades - result1.winning_trades:+<15}"
        )
        print(
            f"{'虧損次數':<25} {result1.losing_trades:<20} {result2.losing_trades:<20} {result2.losing_trades - result1.losing_trades:+<15}"
        )

        print()

        # 盈虧統計
        print(
            f"{'總盈虧 (TWD)':<25} {result1.total_pnl_twd:<19,.0f} {result2.total_pnl_twd:<19,.0f} {result2.total_pnl_twd - result1.total_pnl_twd:+,.0f}"
        )
        print(
            f"{'總盈虧 (點)':<25} {result1.total_pnl_points:<19,.1f} {result2.total_pnl_points:<19,.1f} {result2.total_pnl_points - result1.total_pnl_points:+,.1f}"
        )
        print(
            f"{'毛利 (TWD)':<25} {result1.gross_profit:<19,.0f} {result2.gross_profit:<19,.0f} {result2.gross_profit - result1.gross_profit:+,.0f}"
        )
        print(
            f"{'毛損 (TWD)':<25} {result1.gross_loss:<19,.0f} {result2.gross_loss:<19,.0f} {result2.gross_loss - result1.gross_loss:+,.0f}"
        )

        # 計算獲利因子
        profit_factor1 = (
            result1.gross_profit / result1.gross_loss
            if result1.gross_loss > 0
            else float("inf")
        )
        profit_factor2 = (
            result2.gross_profit / result2.gross_loss
            if result2.gross_loss > 0
            else float("inf")
        )
        print(
            f"{'獲利因子':<25} {profit_factor1:<19.2f} {profit_factor2:<19.2f} {profit_factor2 - profit_factor1:+.2f}"
        )

        print()

        # 風險指標
        print(
            f"{'最大回撤 (TWD)':<25} {result1.max_drawdown:<19,.0f} {result2.max_drawdown:<19,.0f} {result2.max_drawdown - result1.max_drawdown:+,.0f}"
        )
        print(
            f"{'夏普比率':<25} {result1.sharpe_ratio:<19.2f} {result2.sharpe_ratio:<19.2f} {result2.sharpe_ratio - result1.sharpe_ratio:+.2f}"
        )

        print()
        print("-" * 80)

        # 平均統計
        avg_win1 = (
            result1.gross_profit / result1.winning_trades
            if result1.winning_trades > 0
            else 0
        )
        avg_win2 = (
            result2.gross_profit / result2.winning_trades
            if result2.winning_trades > 0
            else 0
        )
        avg_loss1 = (
            result1.gross_loss / result1.losing_trades
            if result1.losing_trades > 0
            else 0
        )
        avg_loss2 = (
            result2.gross_loss / result2.losing_trades
            if result2.losing_trades > 0
            else 0
        )

        print(
            f"{'平均獲利 (TWD)':<25} {avg_win1:<19,.0f} {avg_win2:<19,.0f} {avg_win2 - avg_win1:+,.0f}"
        )
        print(
            f"{'平均虧損 (TWD)':<25} {avg_loss1:<19,.0f} {avg_loss2:<19,.0f} {avg_loss2 - avg_loss1:+,.0f}"
        )

        # 賺賠比
        win_loss_ratio1 = avg_win1 / avg_loss1 if avg_loss1 > 0 else 0
        win_loss_ratio2 = avg_win2 / avg_loss2 if avg_loss2 > 0 else 0
        print(
            f"{'賺賠比':<25} {win_loss_ratio1:<19.2f} {win_loss_ratio2:<19.2f} {win_loss_ratio2 - win_loss_ratio1:+.2f}"
        )

        print()
        print("=" * 80)

        # 結論
        print()
        print("📝 結論:")
        print()

        if result2.total_pnl_twd > result1.total_pnl_twd:
            diff_pnl = result2.total_pnl_twd - result1.total_pnl_twd
            diff_pct = (
                (diff_pnl / abs(result1.total_pnl_twd) * 100)
                if result1.total_pnl_twd != 0
                else 0
            )
            print("✅ MACD 快速停損策略表現較好")
            print(f"   總盈虧提升: {diff_pnl:+,.0f} TWD ({diff_pct:+.1f}%)")
        elif result2.total_pnl_twd < result1.total_pnl_twd:
            diff_pnl = result1.total_pnl_twd - result2.total_pnl_twd
            diff_pct = (
                (diff_pnl / abs(result1.total_pnl_twd) * 100)
                if result1.total_pnl_twd != 0
                else 0
            )
            print("❌ MACD 快速停損策略表現較差")
            print(f"   總盈虧下降: {diff_pnl:,.0f} TWD ({diff_pct:.1f}%)")
        else:
            print("➖ 兩種策略表現相同")

        print()

        if result2.total_trades > result1.total_trades:
            print(
                f"⚠️  快速停損增加了 {result2.total_trades - result1.total_trades} 次交易"
            )
            extra_commission = (
                (result2.total_trades - result1.total_trades) * 2 * 60
            )  # 假設每次60元手續費
            print(f"   額外手續費約: {extra_commission:,.0f} TWD")

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
