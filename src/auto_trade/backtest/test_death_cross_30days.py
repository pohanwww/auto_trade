"""測試不同強死叉門檻的回測比較（前30天）"""

from datetime import datetime, timedelta

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.models.backtest import BacktestConfig
from auto_trade.services.backtest_service import BacktestService
from auto_trade.services.market_service import MarketService
from auto_trade.services.strategy_service import StrategyService


def main():
    """執行不同強死叉門檻的回測比較（前30天）"""

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

    # 基礎配置（只測試前30天）
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

    # 測試不同的強死叉門檻 + 原始版本（無 FS）
    test_configs = [
        {"threshold": None, "name": "原始版本（無 FS）", "enable_fs": False},
        {"threshold": 0.0, "name": "無過濾 FS", "enable_fs": True},
        {"threshold": 3.0, "name": "門檻 3.0", "enable_fs": True},
        {"threshold": 5.0, "name": "門檻 5.0", "enable_fs": True},
    ]
    results = {}

    print("=" * 80)
    print("🔬 測試不同強死叉門檻的影響（前30天牛市階段）")
    print("=" * 80)
    print(
        f"📅 測試期間: {start_date.strftime('%Y-%m-%d')} - {end_date.strftime('%Y-%m-%d')}"
    )
    print()

    for test_config in test_configs:
        threshold = test_config["threshold"]
        name = test_config["name"]
        enable_fs = test_config["enable_fs"]

        print(f"\n{'=' * 80}")
        print(f"📊 測試: {name}")
        print(f"{'=' * 80}\n")

        # 創建配置副本
        config_copy = BacktestConfig(
            symbol=base_config.symbol,
            sub_symbol=base_config.sub_symbol,
            timeframe=base_config.timeframe,
            start_date=base_config.start_date,
            end_date=base_config.end_date,
            initial_capital=base_config.initial_capital,
            order_quantity=base_config.order_quantity,
            stop_loss_points=base_config.stop_loss_points,
            start_trailing_stop_points=base_config.start_trailing_stop_points,
            trailing_stop_points=base_config.trailing_stop_points,
            trailing_stop_points_rate=base_config.trailing_stop_points_rate,
            take_profit_points=base_config.take_profit_points,
            take_profit_points_rate=base_config.take_profit_points_rate,
            enable_trailing_stop=base_config.enable_trailing_stop,
            enable_take_profit=base_config.enable_take_profit,
            enable_macd_fast_stop=enable_fs,
        )

        # 如果啟用 FS，暫時修改 strategy_service 的 check_death_cross 行為
        if enable_fs and threshold is not None:
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
        result = backtest_service.run_backtest(config_copy)
        results[name] = result

        # 恢復原始方法
        if enable_fs and threshold is not None:
            strategy_service.check_death_cross = original_check_death_cross

        # 顯示簡要結果
        print(f"\n📈 結果摘要（{name}）:")
        print(f"   總交易次數: {result.total_trades}")
        print(f"   獲利交易: {result.winning_trades}")
        print(f"   虧損交易: {result.losing_trades}")
        print(f"   勝率: {result.win_rate:.2f}%")
        print(f"   總盈虧: {result.total_pnl_twd:,.0f} TWD")
        print(f"   總獲利: {result.gross_profit:,.0f} TWD")
        print(f"   總虧損: {result.gross_loss:,.0f} TWD")
        print(f"   最大回撤: {result.max_drawdown:.2f}%")
        print(f"   盈虧比: {result.profit_factor:.2f}")

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
    print("📊 比較總結（前30天牛市階段）")
    print("=" * 80)
    print(
        f"\n{'策略':<20} {'交易':<8} {'獲利/虧損':<12} {'勝率':<10} {'總盈虧':<15} {'總獲利':<15} {'總虧損':<15} {'回撤':<10} {'盈虧比':<8}"
    )
    print("-" * 140)

    for name in [tc["name"] for tc in test_configs]:
        result = results[name]
        print(
            f"{name:<20} {result.total_trades:<8} "
            f"{result.winning_trades}/{result.losing_trades:<10} "
            f"{result.win_rate:<9.2f}% "
            f"{result.total_pnl_twd:<14,.0f} "
            f"{result.gross_profit:<14,.0f} "
            f"{result.gross_loss:<14,.0f} "
            f"{result.max_drawdown:<9.2f}% "
            f"{result.profit_factor:<8.2f}"
        )

    # 找出最佳策略
    best_name = max(results.keys(), key=lambda n: results[n].total_pnl_twd)
    best_result = results[best_name]

    print("\n" + "=" * 80)
    print(f"🏆 最佳策略: {best_name}")
    print(f"   總盈虧: {best_result.total_pnl_twd:,.0f} TWD")
    print(f"   勝率: {best_result.win_rate:.2f}%")
    print(f"   最大回撤: {best_result.max_drawdown:.2f}%")
    print(f"   盈虧比: {best_result.profit_factor:.2f}")
    print("=" * 80)

    # 分析快速停損（FS）的效果
    print("\n" + "=" * 80)
    print("⚡ 快速停損（FS）效果分析")
    print("=" * 80)
    for name in [tc["name"] for tc in test_configs]:
        result = results[name]
        fs_count = sum(1 for trade in result.trades if trade.exit_reason.value == "FS")
        if fs_count > 0:
            fs_pnl = sum(
                trade.pnl_twd
                for trade in result.trades
                if trade.exit_reason.value == "FS"
            )
            print(f"\n{name}:")
            print(f"   FS 次數: {fs_count}")
            print(f"   FS 總盈虧: {fs_pnl:,.0f} TWD")
            print(f"   FS 平均虧損: {fs_pnl / fs_count:,.0f} TWD")
        else:
            print(f"\n{name}:")
            print("   無 FS 觸發")

    # 比較無過濾 FS vs 原始版本
    if "無過濾 FS" in results and "原始版本（無 FS）" in results:
        fs_result = results["無過濾 FS"]
        orig_result = results["原始版本（無 FS）"]

        print("\n" + "=" * 80)
        print("📊 無過濾 FS vs 原始版本（無 FS）詳細比較")
        print("=" * 80)

        print(f"\n{'指標':<20} {'原始版本':<20} {'無過濾 FS':<20} {'差異':<20}")
        print("-" * 80)
        print(
            f"{'總盈虧':<20} {orig_result.total_pnl_twd:>19,.0f} {fs_result.total_pnl_twd:>19,.0f} {fs_result.total_pnl_twd - orig_result.total_pnl_twd:>+19,.0f}"
        )
        print(
            f"{'勝率':<20} {orig_result.win_rate:>18.2f}% {fs_result.win_rate:>18.2f}% {fs_result.win_rate - orig_result.win_rate:>+18.2f}%"
        )
        print(
            f"{'最大回撤':<20} {orig_result.max_drawdown:>18.2f}% {fs_result.max_drawdown:>18.2f}% {fs_result.max_drawdown - orig_result.max_drawdown:>+18.2f}%"
        )
        print(
            f"{'盈虧比':<20} {orig_result.profit_factor:>19.2f} {fs_result.profit_factor:>19.2f} {fs_result.profit_factor - orig_result.profit_factor:>+19.2f}"
        )
        print(
            f"{'交易次數':<20} {orig_result.total_trades:>19} {fs_result.total_trades:>19} {fs_result.total_trades - orig_result.total_trades:>+19}"
        )

        improvement = (
            (
                (fs_result.total_pnl_twd - orig_result.total_pnl_twd)
                / orig_result.total_pnl_twd
                * 100
            )
            if orig_result.total_pnl_twd != 0
            else 0
        )
        print(f"\n💰 盈虧改善: {improvement:+.2f}%")


if __name__ == "__main__":
    main()
