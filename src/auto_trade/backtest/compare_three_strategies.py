"""
比較三種策略的 90 天表現

策略：
1. 原始策略（無 MACD 快速停損）
2. 快速停損 - 無過濾（加速度 0.0，所有死叉）
3. 快速停損 - 強死叉（加速度 ≥ 3.0）
"""

from datetime import datetime

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.models.backtest import BacktestConfig
from auto_trade.services.backtest_service import BacktestService
from auto_trade.services.market_service import MarketService
from auto_trade.services.strategy_service import StrategyService


def main():
    """比較三種策略的 90 天表現"""

    print("=" * 80)
    print("🔬 三策略比較：原始 vs 無過濾快速停損 vs 強死叉快速停損（90天）")
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

    # ===== 策略 1: 原始策略（無 MACD 快速停損） =====
    print("=" * 80)
    print("📊 策略 1: 原始策略（無 MACD 快速停損）")
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
        enable_macd_fast_stop=False,  # 不啟用快速停損
    )

    print("啟用 MACD 快速停損：否\n")

    result1 = backtest_service.run_backtest(config1)
    backtest_service.save_results(
        result1, filename="backtest_results_MXF_90days_original.txt"
    )

    # ===== 策略 2: 快速停損 - 無過濾（加速度 0.0） =====
    print("\n" + "=" * 80)
    print("📊 策略 2: 快速停損 - 無過濾（所有死叉）")
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
        min_acceleration_threshold=0.0,  # 無過濾
    )

    print("啟用 MACD 快速停損：是")
    print("加速度門檻：無過濾（所有死叉）\n")

    result2 = backtest_service.run_backtest(config2)
    backtest_service.save_results(
        result2, filename="backtest_results_MXF_90days_no_filter.txt"
    )

    # ===== 策略 3: 快速停損 - 強死叉（加速度 3.0） =====
    print("\n" + "=" * 80)
    print("📊 策略 3: 快速停損 - 強死叉（加速度 ≥ 3.0）")
    print("-" * 80)

    config3 = BacktestConfig(
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

    result3 = backtest_service.run_backtest(config3)
    backtest_service.save_results(
        result3, filename="backtest_results_MXF_90days_strong_filter.txt"
    )

    # ===== 生成比較報告 =====
    print("\n" + "=" * 80)
    print("📊 三策略比較結果（90 天完整期間）")
    print("=" * 80)

    # 基本統計
    print(f"\n{'指標':<20} {'原始策略':<20} {'無過濾FS':<20} {'強死叉FS':<20}")
    print("-" * 80)

    print(
        f"{'總交易次數':<20} {result1.total_trades:<20} {result2.total_trades:<20} {result3.total_trades:<20}"
    )
    print(
        f"{'獲利交易':<20} {result1.winning_trades:<20} {result2.winning_trades:<20} {result3.winning_trades:<20}"
    )
    print(
        f"{'虧損交易':<20} {result1.losing_trades:<20} {result2.losing_trades:<20} {result3.losing_trades:<20}"
    )
    print(
        f"{'勝率':<20} {result1.win_rate:<19.2f}% {result2.win_rate:<19.2f}% {result3.win_rate:<19.2f}%"
    )

    # 盈虧統計
    print("\n" + "-" * 80)
    print(
        f"{'總盈虧 (TWD)':<20} {result1.total_pnl_twd:<19,.0f} {result2.total_pnl_twd:<19,.0f} {result3.total_pnl_twd:<19,.0f}"
    )
    print(
        f"{'總獲利 (TWD)':<20} {result1.gross_profit:<19,.0f} {result2.gross_profit:<19,.0f} {result3.gross_profit:<19,.0f}"
    )
    print(
        f"{'總虧損 (TWD)':<20} {result1.gross_loss:<19,.0f} {result2.gross_loss:<19,.0f} {result3.gross_loss:<19,.0f}"
    )

    # 風險指標
    print("\n" + "-" * 80)
    print(
        f"{'最大回撤':<20} {result1.max_drawdown:<19.2f}% {result2.max_drawdown:<19.2f}% {result3.max_drawdown:<19.2f}%"
    )
    print(
        f"{'盈虧比':<20} {result1.profit_factor:<20.2f} {result2.profit_factor:<20.2f} {result3.profit_factor:<20.2f}"
    )
    print(
        f"{'夏普比率':<20} {result1.sharpe_ratio:<20.2f} {result2.sharpe_ratio:<20.2f} {result3.sharpe_ratio:<20.2f}"
    )
    print(
        f"{'平均持倉(小時)':<20} {result1.avg_trade_duration_hours:<20.1f} {result2.avg_trade_duration_hours:<20.1f} {result3.avg_trade_duration_hours:<20.1f}"
    )

    # 相對於原始策略的改善
    print("\n" + "=" * 80)
    print("📈 相對於原始策略的改善")
    print("=" * 80)

    # 無過濾 vs 原始
    pnl_diff_2 = result2.total_pnl_twd - result1.total_pnl_twd
    pnl_pct_2 = (
        (pnl_diff_2 / result1.total_pnl_twd * 100) if result1.total_pnl_twd != 0 else 0
    )
    loss_diff_2 = result2.gross_loss - result1.gross_loss
    loss_pct_2 = (
        (loss_diff_2 / result1.gross_loss * 100) if result1.gross_loss != 0 else 0
    )
    dd_diff_2 = result2.max_drawdown - result1.max_drawdown

    print("\n無過濾快速停損 vs 原始策略:")
    print(f"   總盈虧：{pnl_diff_2:+,.0f} TWD ({pnl_pct_2:+.2f}%)")
    print(f"   總虧損：{loss_diff_2:+,.0f} TWD ({loss_pct_2:+.2f}%)")
    print(
        f"   最大回撤：{dd_diff_2:+.2f}% ({(dd_diff_2 / result1.max_drawdown * 100):+.2f}%)"
    )
    print(
        f"   盈虧比：{result2.profit_factor - result1.profit_factor:+.2f} ({(result2.profit_factor - result1.profit_factor) / result1.profit_factor * 100:+.2f}%)"
    )

    # 強死叉 vs 原始
    pnl_diff_3 = result3.total_pnl_twd - result1.total_pnl_twd
    pnl_pct_3 = (
        (pnl_diff_3 / result1.total_pnl_twd * 100) if result1.total_pnl_twd != 0 else 0
    )
    loss_diff_3 = result3.gross_loss - result1.gross_loss
    loss_pct_3 = (
        (loss_diff_3 / result1.gross_loss * 100) if result1.gross_loss != 0 else 0
    )
    dd_diff_3 = result3.max_drawdown - result1.max_drawdown

    print("\n強死叉快速停損 vs 原始策略:")
    print(f"   總盈虧：{pnl_diff_3:+,.0f} TWD ({pnl_pct_3:+.2f}%)")
    print(f"   總虧損：{loss_diff_3:+,.0f} TWD ({loss_pct_3:+.2f}%)")
    print(
        f"   最大回撤：{dd_diff_3:+.2f}% ({(dd_diff_3 / result1.max_drawdown * 100):+.2f}%)"
    )
    print(
        f"   盈虧比：{result3.profit_factor - result1.profit_factor:+.2f} ({(result3.profit_factor - result1.profit_factor) / result1.profit_factor * 100:+.2f}%)"
    )

    # 退出原因統計
    print("\n" + "=" * 80)
    print("⚡ 快速停損（FS）效果分析")
    print("=" * 80)

    # 策略 2
    fs_count2 = sum(1 for trade in result2.trades if trade.exit_reason.value == "FS")
    sl_count2 = sum(1 for trade in result2.trades if trade.exit_reason.value == "SL")
    print("\n策略 2（無過濾）:")
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

    # 策略 3
    fs_count3 = sum(1 for trade in result3.trades if trade.exit_reason.value == "FS")
    sl_count3 = sum(1 for trade in result3.trades if trade.exit_reason.value == "SL")
    print("\n策略 3（強死叉 ≥ 3.0）:")
    print(f"   FS 次數: {fs_count3}")
    if fs_count3 > 0:
        fs_pnl3 = sum(
            trade.pnl_twd for trade in result3.trades if trade.exit_reason.value == "FS"
        )
        print(f"   FS 總盈虧: {fs_pnl3:,.0f} TWD")
        print(f"   FS 平均虧損: {fs_pnl3 / fs_count3:,.0f} TWD")
    print(f"   SL 次數: {sl_count3}")
    if sl_count3 > 0:
        sl_pnl3 = sum(
            trade.pnl_twd for trade in result3.trades if trade.exit_reason.value == "SL"
        )
        print(f"   SL 總盈虧: {sl_pnl3:,.0f} TWD")
        print(f"   SL 平均虧損: {sl_pnl3 / sl_count3:,.0f} TWD")

    # 結論
    print("\n" + "=" * 80)
    print("🏆 結論與建議")
    print("=" * 80)

    # 找出最佳策略
    results = [
        ("原始策略", result1.total_pnl_twd),
        ("無過濾快速停損", result2.total_pnl_twd),
        ("強死叉快速停損", result3.total_pnl_twd),
    ]
    best_strategy = max(results, key=lambda x: x[1])

    print(f"\n✨ 總盈虧最高：{best_strategy[0]} ({best_strategy[1]:,.0f} TWD)")

    # 風險控制
    dd_results = [
        ("原始策略", result1.max_drawdown),
        ("無過濾快速停損", result2.max_drawdown),
        ("強死叉快速停損", result3.max_drawdown),
    ]
    best_dd = min(dd_results, key=lambda x: x[1])
    print(f"✨ 風險控制最佳：{best_dd[0]} (回撤 {best_dd[1]:.2f}%)")

    # 盈虧比
    pf_results = [
        ("原始策略", result1.profit_factor),
        ("無過濾快速停損", result2.profit_factor),
        ("強死叉快速停損", result3.profit_factor),
    ]
    best_pf = max(pf_results, key=lambda x: x[1])
    print(f"✨ 盈虧比最高：{best_pf[0]} ({best_pf[1]:.2f})")

    # 勝率
    wr_results = [
        ("原始策略", result1.win_rate),
        ("無過濾快速停損", result2.win_rate),
        ("強死叉快速停損", result3.win_rate),
    ]
    best_wr = max(wr_results, key=lambda x: x[1])
    print(f"✨ 勝率最高：{best_wr[0]} ({best_wr[1]:.2f}%)")

    print("\n" + "=" * 80)
    print("✅ 比較完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()
