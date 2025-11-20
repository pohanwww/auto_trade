"""
測試不同死叉加速度門檻的回測腳本 - 90 天完整期間

測試過去 90 天（包含牛市和盤整期）的表現
比較加速度門檻：0.0, 1.0, 2.0, 3.0, 4.0, 5.0
"""

from datetime import datetime

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.models.backtest import BacktestConfig
from auto_trade.services.backtest_service import BacktestService
from auto_trade.services.market_service import MarketService
from auto_trade.services.strategy_service import StrategyService


def main():
    """執行不同死叉加速度門檻的回測比較 - 90天"""

    print("=" * 80)
    print("🔬 死叉加速度門檻測試 - 過去 90 天完整期間")
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

    # 設置回測時間範圍（2025-08-15 到 2025-11-13，共90天）
    start_date = datetime(2025, 8, 15)
    end_date = datetime(2025, 11, 13)

    print(f"\n📅 回測期間：{start_date.date()} 至 {end_date.date()}")
    print("📊 測試商品：MXF 202511")
    print("⏰ K線週期：30 分鐘\n")

    # 測試不同的加速度門檻
    thresholds = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    results = {}

    for threshold in thresholds:
        print("=" * 80)
        threshold_label = "無過濾" if threshold == 0.0 else f"{threshold:.1f}"
        print(f"🧪 測試加速度門檻：{threshold_label}")
        print("=" * 80)

        # 創建回測配置
        config_backtest = BacktestConfig(
            symbol="MXF",
            sub_symbol="MXF202511",  # 小台指202511合約
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
            enable_macd_fast_stop=True,  # 啟用 MACD 快速停損
            min_acceleration_threshold=threshold,  # 加速度門檻
        )

        # 創建回測服務並執行
        backtest_service = BacktestService(
            market_service=market_service,
            strategy_service=strategy_service,
        )

        result = backtest_service.run_backtest(config_backtest)
        results[threshold] = result

        # 保存詳細結果
        filename = f"backtest_results_MXF_90days_acceleration_{threshold:.1f}.txt"
        backtest_service.save_results(result, filename=filename)

        # 顯示簡要結果
        print(f"\n📈 結果摘要（加速度門檻 {threshold_label}）:")
        print(f"   總交易次數: {result.total_trades}")
        print(f"   獲利交易: {result.winning_trades}")
        print(f"   虧損交易: {result.losing_trades}")
        print(f"   勝率: {result.win_rate:.2f}%")
        print(f"   總盈虧: {result.total_pnl_twd:,.0f} TWD")
        print(f"   總獲利: {result.gross_profit:,.0f} TWD")
        print(f"   總虧損: {result.gross_loss:,.0f} TWD")
        print(f"   最大回撤: {result.max_drawdown:.2f}%")
        print(f"   盈虧比: {result.profit_factor:.2f}")
        print(f"   平均持倉時間: {result.avg_trade_duration_hours:.1f} 小時")

        # 統計不同退出原因的次數
        exit_reasons = {}
        for trade in result.trades:
            reason = trade.exit_reason.value if trade.exit_reason else "Unknown"
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        print("   退出原因統計:")
        for reason, count in sorted(exit_reasons.items()):
            print(f"      {reason}: {count}")

        print(f"\n✅ 加速度 {threshold_label} 回測完成\n")

    # 生成比較報告
    print("\n" + "=" * 80)
    print("📊 加速度門檻比較結果（過去 90 天完整期間）")
    print("=" * 80)
    print(
        f"\n{'門檻':<10} {'交易':<8} {'獲利/虧損':<12} {'勝率':<10} {'總盈虧':<15} {'總獲利':<15} {'總虧損':<15} {'回撤':<10} {'盈虧比':<8}"
    )
    print("-" * 130)

    for threshold in thresholds:
        result = results[threshold]
        threshold_label = "無過濾" if threshold == 0.0 else f"{threshold:.1f}"
        print(
            f"{threshold_label:<10} {result.total_trades:<8} "
            f"{result.winning_trades}/{result.losing_trades:<10} "
            f"{result.win_rate:<9.2f}% "
            f"{result.total_pnl_twd:<14,.0f} "
            f"{result.gross_profit:<14,.0f} "
            f"{result.gross_loss:<14,.0f} "
            f"{result.max_drawdown:<9.2f}% "
            f"{result.profit_factor:<8.2f}"
        )

    # 找出最佳門檻
    best_threshold = max(results.keys(), key=lambda t: results[t].total_pnl_twd)
    best_result = results[best_threshold]

    print("\n" + "=" * 80)
    best_label = "無過濾" if best_threshold == 0.0 else f"{best_threshold:.1f}"
    print(f"🏆 最佳門檻: {best_label}")
    print(f"   總盈虧: {best_result.total_pnl_twd:,.0f} TWD")
    print(f"   勝率: {best_result.win_rate:.2f}%")
    print(f"   最大回撤: {best_result.max_drawdown:.2f}%")
    print(f"   盈虧比: {best_result.profit_factor:.2f}")
    print("=" * 80)

    # 分析快速停損（FS）的效果
    print("\n" + "=" * 80)
    print("⚡ 快速停損（FS）效果分析")
    print("=" * 80)
    for threshold in thresholds:
        result = results[threshold]
        fs_count = sum(1 for trade in result.trades if trade.exit_reason.value == "FS")
        threshold_label = "無過濾" if threshold == 0.0 else f"{threshold:.1f}"

        print(f"\n門檻 {threshold_label}:")
        print(f"   FS 次數: {fs_count}")
        if fs_count > 0:
            fs_pnl = sum(
                trade.pnl_twd
                for trade in result.trades
                if trade.exit_reason.value == "FS"
            )
            print(f"   FS 總盈虧: {fs_pnl:,.0f} TWD")
            print(f"   FS 平均虧損: {fs_pnl / fs_count:,.0f} TWD")

        # 統計 SL 次數和平均虧損
        sl_count = sum(1 for trade in result.trades if trade.exit_reason.value == "SL")
        if sl_count > 0:
            sl_pnl = sum(
                trade.pnl_twd
                for trade in result.trades
                if trade.exit_reason.value == "SL"
            )
            print(f"   SL 次數: {sl_count}")
            print(f"   SL 總盈虧: {sl_pnl:,.0f} TWD")
            print(f"   SL 平均虧損: {sl_pnl / sl_count:,.0f} TWD")

    # 詳細比較
    print("\n" + "=" * 80)
    print("📊 門檻對比分析")
    print("=" * 80)

    base_result = results[0.0]
    print("\n以「無過濾」為基準的比較：")
    print(f"{'門檻':<10} {'總盈虧差異':<20} {'虧損差異':<20} {'FS次數差異':<15}")
    print("-" * 65)

    for threshold in thresholds:
        result = results[threshold]
        threshold_label = "無過濾" if threshold == 0.0 else f"{threshold:.1f}"

        pnl_diff = result.total_pnl_twd - base_result.total_pnl_twd
        loss_diff = result.gross_loss - base_result.gross_loss

        fs_count = sum(1 for trade in result.trades if trade.exit_reason.value == "FS")
        base_fs_count = sum(
            1 for trade in base_result.trades if trade.exit_reason.value == "FS"
        )
        fs_diff = fs_count - base_fs_count

        print(
            f"{threshold_label:<10} {pnl_diff:>+19,.0f} {loss_diff:>+19,.0f} {fs_diff:>+14}"
        )

    print("\n" + "=" * 80)
    print("✅ 所有回測完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()
