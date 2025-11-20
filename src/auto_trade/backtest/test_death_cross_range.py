"""測試不同強死叉門檻的回測比較（最近30天盤整期）"""

from datetime import datetime, timedelta

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.models.backtest import BacktestConfig
from auto_trade.services.backtest_service import BacktestService
from auto_trade.services.market_service import MarketService
from auto_trade.services.strategy_service import StrategyService


def main():
    """執行不同強死叉門檻的回測比較（最近30天盤整期）"""

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

    # 初始化服務
    market_service = MarketService(api_client)
    strategy_service = StrategyService()
    backtest_service = BacktestService(market_service, strategy_service)

    # 基礎配置（測試最近30天 - 盤整期）
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)

    base_config = BacktestConfig(
        symbol="MXF",
        sub_symbol="MXF202511",
        timeframe="30m",
        start_date=start_date,
        end_date=end_date,
        initial_capital=1_000_000,
        order_quantity=2,
        stop_loss_points=80,
        start_trailing_stop_points=250,
        trailing_stop_points=250,
        trailing_stop_points_rate=0.0095,
        take_profit_points=500,
        take_profit_points_rate=0.02,
        enable_trailing_stop=True,
        enable_take_profit=True,
        enable_macd_fast_stop=True,
    )

    # 測試不同的強死叉門檻
    thresholds = [0.0, 1.0, 2.0, 3.0]
    results = {}

    print("=" * 80)
    print("🔬 測試不同強死叉門檻的影響（最近30天盤整震盪期）")
    print("=" * 80)
    print(
        f"📅 測試期間: {start_date.strftime('%Y-%m-%d')} - {end_date.strftime('%Y-%m-%d')}"
    )
    print()

    for threshold in thresholds:
        print(f"\n{'=' * 80}")
        if threshold == 0.0:
            print(f"📊 測試門檻: {threshold}（無過濾，所有死叉都觸發快速停損）")
        else:
            print(f"📊 測試門檻: {threshold}")
        print(f"{'=' * 80}\n")

        # 暫時修改 strategy_service 的 check_death_cross 行為
        original_check_death_cross = strategy_service.check_death_cross

        def check_death_cross_with_threshold(macd_list, min_strength=None):
            """包裝原始方法，使用測試門檻"""
            if threshold == 0.0:
                # 無門檻，所有死叉都返回 True
                return original_check_death_cross(macd_list, min_strength=None)
            else:
                # 使用指定門檻
                return original_check_death_cross(macd_list, min_strength=threshold)

        # 替換方法
        strategy_service.check_death_cross = check_death_cross_with_threshold

        # 運行回測
        result = backtest_service.run_backtest(base_config)
        results[threshold] = result

        # 恢復原始方法
        strategy_service.check_death_cross = original_check_death_cross

        # 顯示簡要結果
        print(f"\n📈 結果摘要（門檻 {threshold}）:")
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

    # 比較結果
    print("\n" + "=" * 80)
    print("📊 不同門檻比較總結（盤整震盪期）")
    print("=" * 80)
    print(
        f"\n{'門檻':<10} {'交易':<8} {'獲利/虧損':<12} {'勝率':<10} {'總盈虧':<15} {'總獲利':<15} {'總虧損':<15} {'回撤':<10} {'盈虧比':<8}"
    )
    print("-" * 130)

    for threshold in thresholds:
        result = results[threshold]
        threshold_label = f"{threshold:.1f}" if threshold > 0 else "無過濾"
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
    best_label = "無過濾" if best_threshold == 0.0 else f"{best_threshold}"
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


if __name__ == "__main__":
    main()
