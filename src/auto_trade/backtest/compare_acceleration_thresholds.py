"""
比較不同加速度門檻的快速停損策略

比較：
1. 無過濾（加速度 0.0）- 所有死叉都觸發快速停損
2. 強死叉（加速度 3.0）- 只有加速度 >= 3.0 的死叉才觸發快速停損
"""

from datetime import datetime

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.models.backtest import BacktestConfig
from auto_trade.services.backtest_service import BacktestService
from auto_trade.services.market_service import MarketService
from auto_trade.services.strategy_service import StrategyService


def main():
    """比較不同加速度門檻的快速停損策略"""

    print("=" * 80)
    print("🔬 快速停損策略比較：無過濾 vs 強死叉（加速度 > 3.0）")
    print("=" * 80)

    # 加載配置
    config = Config()

    # 創建 API 客戶端（模擬模式）
    api_client = create_api_client(
        api_key=config.api_key,
        secret_key=config.secret_key,
        ca_path=config.ca_cert_path,
        ca_passwd=config.ca_password,
        simulation=True,
    )

    # 創建服務
    market_service = MarketService(api_client)
    strategy_service = StrategyService()
    backtest_service = BacktestService(
        market_service=market_service,
        strategy_service=strategy_service,
    )

    # 設置回測時間範圍（2025-08-15 到 2025-11-13，共90天）
    start_date = datetime(2025, 8, 15)
    end_date = datetime(2025, 11, 13)

    print(f"\n📅 回測期間：{start_date.date()} 至 {end_date.date()}")
    print("📊 測試商品：MXF 202511")
    print("⏰ K線週期：30 分鐘")
    print("⚙️  回測天數：90 天\n")

    # ===== 策略 1: 無過濾（加速度 0.0） =====
    print("=" * 80)
    print("📊 策略 1: 快速停損 - 無過濾（所有死叉）")
    print("-" * 80)

    config1 = BacktestConfig(
        symbol="MXF",
        sub_symbol="MXF202511",
        start_date=start_date,
        end_date=end_date,
        initial_capital=1000000,
        order_quantity=2,
        timeframe="30m",
        stop_loss_points=80,
        start_trailing_stop_points=250,
        trailing_stop_points=250,
        take_profit_points=500,
        trailing_stop_points_rate=0.0095,
        take_profit_points_rate=0.02,
        enable_macd_fast_stop=True,
        min_acceleration_threshold=0.0,  # 無過濾
    )

    print("啟用 MACD 快速停損：是")
    print("加速度門檻：無過濾（所有死叉）\n")

    result1 = backtest_service.run_backtest(config1)
    backtest_service.save_results(
        result1, filename="backtest_results_MXF_90days_no_filter.txt"
    )

    # ===== 策略 2: 強死叉（加速度 3.0） =====
    print("\n" + "=" * 80)
    print("📊 策略 2: 快速停損 - 強死叉過濾（加速度 ≥ 3.0）")
    print("-" * 80)

    config2 = BacktestConfig(
        symbol="MXF",
        sub_symbol="MXF202511",
        start_date=start_date,
        end_date=end_date,
        initial_capital=1000000,
        order_quantity=2,
        timeframe="30m",
        stop_loss_points=80,
        start_trailing_stop_points=250,
        trailing_stop_points=250,
        take_profit_points=500,
        trailing_stop_points_rate=0.0095,
        take_profit_points_rate=0.02,
        enable_macd_fast_stop=True,
        min_acceleration_threshold=3.0,  # 強死叉過濾
    )

    print("啟用 MACD 快速停損：是")
    print("加速度門檻：3.0（強死叉）\n")

    result2 = backtest_service.run_backtest(config2)
    backtest_service.save_results(
        result2, filename="backtest_results_MXF_90days_strong_filter.txt"
    )

    # ===== 生成比較報告 =====
    print("\n" + "=" * 80)
    print("📊 策略比較結果（90 天完整期間）")
    print("=" * 80)

    # 基本統計
    print(f"\n{'指標':<20} {'無過濾':<20} {'強死叉(≥3.0)':<20} {'差異':<20}")
    print("-" * 80)

    print(
        f"{'總交易次數':<20} {result1.total_trades:<20} {result2.total_trades:<20} {result2.total_trades - result1.total_trades:+d}"
    )
    print(
        f"{'獲利交易':<20} {result1.winning_trades:<20} {result2.winning_trades:<20} {result2.winning_trades - result1.winning_trades:+d}"
    )
    print(
        f"{'虧損交易':<20} {result1.losing_trades:<20} {result2.losing_trades:<20} {result2.losing_trades - result1.losing_trades:+d}"
    )
    print(
        f"{'勝率':<20} {result1.win_rate:<19.2f}% {result2.win_rate:<19.2f}% {result2.win_rate - result1.win_rate:+.2f}%"
    )

    # 盈虧統計
    print("\n" + "-" * 80)
    pnl_diff = result2.total_pnl_twd - result1.total_pnl_twd
    pnl_pct = (
        (pnl_diff / result1.total_pnl_twd * 100) if result1.total_pnl_twd != 0 else 0
    )
    print(
        f"{'總盈虧 (TWD)':<20} {result1.total_pnl_twd:<19,.0f} {result2.total_pnl_twd:<19,.0f} {pnl_diff:+19,.0f} ({pnl_pct:+.2f}%)"
    )

    profit_diff = result2.gross_profit - result1.gross_profit
    print(
        f"{'總獲利 (TWD)':<20} {result1.gross_profit:<19,.0f} {result2.gross_profit:<19,.0f} {profit_diff:+19,.0f}"
    )

    loss_diff = result2.gross_loss - result1.gross_loss
    loss_pct = (loss_diff / result1.gross_loss * 100) if result1.gross_loss != 0 else 0
    print(
        f"{'總虧損 (TWD)':<20} {result1.gross_loss:<19,.0f} {result2.gross_loss:<19,.0f} {loss_diff:+19,.0f} ({loss_pct:+.2f}%)"
    )

    # 風險指標
    print("\n" + "-" * 80)
    dd_diff = result2.max_drawdown - result1.max_drawdown
    dd_pct = (dd_diff / result1.max_drawdown * 100) if result1.max_drawdown != 0 else 0
    print(
        f"{'最大回撤':<20} {result1.max_drawdown:<19.2f}% {result2.max_drawdown:<19.2f}% {dd_diff:+.2f}% ({dd_pct:+.2f}%)"
    )

    pf_diff = result2.profit_factor - result1.profit_factor
    pf_pct = (
        (pf_diff / result1.profit_factor * 100) if result1.profit_factor != 0 else 0
    )
    print(
        f"{'盈虧比':<20} {result1.profit_factor:<20.2f} {result2.profit_factor:<20.2f} {pf_diff:+.2f} ({pf_pct:+.2f}%)"
    )

    print(
        f"{'夏普比率':<20} {result1.sharpe_ratio:<20.2f} {result2.sharpe_ratio:<20.2f} {result2.sharpe_ratio - result1.sharpe_ratio:+.2f}"
    )

    # 退出原因統計
    print("\n" + "=" * 80)
    print("⚡ 快速停損（FS）效果分析")
    print("=" * 80)

    # 策略 1
    fs_count1 = sum(1 for trade in result1.trades if trade.exit_reason.value == "FS")
    sl_count1 = sum(1 for trade in result1.trades if trade.exit_reason.value == "SL")
    print("\n策略 1（無過濾）:")
    print(f"   FS 次數: {fs_count1}")
    if fs_count1 > 0:
        fs_pnl1 = sum(
            trade.pnl_twd for trade in result1.trades if trade.exit_reason.value == "FS"
        )
        print(f"   FS 總盈虧: {fs_pnl1:,.0f} TWD")
        print(f"   FS 平均虧損: {fs_pnl1 / fs_count1:,.0f} TWD")
    print(f"   SL 次數: {sl_count1}")
    if sl_count1 > 0:
        sl_pnl1 = sum(
            trade.pnl_twd for trade in result1.trades if trade.exit_reason.value == "SL"
        )
        print(f"   SL 總盈虧: {sl_pnl1:,.0f} TWD")
        print(f"   SL 平均虧損: {sl_pnl1 / sl_count1:,.0f} TWD")

    # 策略 2
    fs_count2 = sum(1 for trade in result2.trades if trade.exit_reason.value == "FS")
    sl_count2 = sum(1 for trade in result2.trades if trade.exit_reason.value == "SL")
    print("\n策略 2（強死叉 ≥ 3.0）:")
    print(f"   FS 次數: {fs_count2}")
    if fs_count2 > 0:
        fs_pnl2 = sum(
            trade.pnl_twd for trade in result2.trades if trade.exit_reason.value == "FS"
        )
        print(f"   FS 總盈虧: {fs_pnl2:,.0f} TWD")
        print(f"   FS 平均虧損: {fs_pnl2 / fs_count2:,.0f} TWD")
    print(f"   SL 次數: {sl_count2}")
    if sl_count2 > 0:
        sl_pnl2 = sum(
            trade.pnl_twd for trade in result2.trades if trade.exit_reason.value == "SL"
        )
        print(f"   SL 總盈虧: {sl_pnl2:,.0f} TWD")
        print(f"   SL 平均虧損: {sl_pnl2 / sl_count2:,.0f} TWD")

    # 差異分析
    print("\n差異:")
    print(f"   FS 次數差異: {fs_count2 - fs_count1:+d}")
    print(f"   SL 次數差異: {sl_count2 - sl_count1:+d}")

    # 結論
    print("\n" + "=" * 80)
    print("🏆 結論")
    print("=" * 80)

    if result1.total_pnl_twd > result2.total_pnl_twd:
        winner = "無過濾"
        advantage = result1.total_pnl_twd - result2.total_pnl_twd
        advantage_pct = (
            (advantage / result2.total_pnl_twd * 100)
            if result2.total_pnl_twd != 0
            else 0
        )
    else:
        winner = "強死叉（≥ 3.0）"
        advantage = result2.total_pnl_twd - result1.total_pnl_twd
        advantage_pct = (
            (advantage / result1.total_pnl_twd * 100)
            if result1.total_pnl_twd != 0
            else 0
        )

    print(f"\n✨ 最佳策略：{winner}")
    print(f"   總盈虧優勢：{advantage:+,.0f} TWD ({advantage_pct:+.2f}%)")

    if result1.max_drawdown < result2.max_drawdown:
        print(
            f"   風險控制：無過濾更優（回撤 {result1.max_drawdown:.2f}% vs {result2.max_drawdown:.2f}%）"
        )
    else:
        print(
            f"   風險控制：強死叉更優（回撤 {result2.max_drawdown:.2f}% vs {result1.max_drawdown:.2f}%）"
        )

    if result1.profit_factor > result2.profit_factor:
        print(
            f"   盈虧比：無過濾更優（{result1.profit_factor:.2f} vs {result2.profit_factor:.2f}）"
        )
    else:
        print(
            f"   盈虧比：強死叉更優（{result2.profit_factor:.2f} vs {result1.profit_factor:.2f}）"
        )

    print("\n" + "=" * 80)
    print("✅ 比較完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()
