"""測試不同強金叉門檻的回測比較"""

from datetime import datetime, timedelta

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.models.backtest import BacktestConfig
from auto_trade.services.backtest_service import BacktestService
from auto_trade.services.market_service import MarketService
from auto_trade.services.strategy_service import StrategyService


def main():
    """執行不同強金叉門檻的回測比較"""

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

    # 基礎配置
    base_config = BacktestConfig(
        symbol="MXF",
        sub_symbol="MXF202511",
        timeframe="30m",
        start_date=datetime.now() - timedelta(days=90),
        end_date=datetime.now(),
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

    # 測試不同的強金叉門檻
    thresholds = [0.0, 1.0, 2.0, 3.0, 5.0]
    results = {}

    print("=" * 80)
    print("🔬 測試不同強金叉門檻的影響")
    print("=" * 80)
    print()

    for threshold in thresholds:
        print(f"\n{'=' * 80}")
        print(f"📊 測試強金叉門檻: {threshold}")
        print(f"{'=' * 80}\n")

        # 修改 backtest_service 中的門檻（臨時修改）
        original_generate_signal = backtest_service._generate_signal

        def generate_signal_with_threshold(kbars, current_price, config):
            """包裝原始方法，注入門檻檢查"""
            from auto_trade.models import Action, TradingSignal

            # 直接使用 KBarList 計算 MACD
            macd_list = strategy_service.calculate_macd(kbars)

            # 取得最新的MACD值
            latest_macd = macd_list.get_latest(3)
            if len(latest_macd) < 3:
                return TradingSignal(
                    action=Action.Hold,
                    symbol=config.symbol,
                    price=current_price,
                    reason="Insufficient MACD data",
                )

            current_macd = latest_macd[-2]
            previous_macd = latest_macd[-3]

            current_signal = current_macd.signal_line
            previous_signal = previous_macd.signal_line

            # MACD金叉策略：MACD < 30 且強金叉時買入
            if (
                (current_macd.macd_line + current_macd.signal_line) / 2 < 30
                and previous_macd.macd_line <= previous_signal
                and current_macd.macd_line > current_signal
            ):
                # 檢查金叉強度
                golden_cross_strength = abs(current_macd.macd_line - current_signal)

                # 只有強金叉才觸發購買
                if golden_cross_strength >= threshold:
                    return TradingSignal(
                        action=Action.Buy,
                        symbol=config.symbol,
                        price=current_price,
                        confidence=0.8,
                        reason=f"強金叉確認（強度 {golden_cross_strength:.2f} >= {threshold}）- MACD({current_macd.macd_line:.2f}) > Signal({current_signal:.2f})",
                        timestamp=datetime.now(),
                    )
                else:
                    # 弱金叉 - 忽略
                    print(
                        f"⚪ 弱金叉（強度 {golden_cross_strength:.2f} < {threshold}）- MACD:{current_macd.macd_line:.1f} > Signal:{current_signal:.1f}，忽略"
                    )

            return TradingSignal(
                action=Action.Hold,
                symbol=config.symbol,
                price=current_price,
                reason="No signal",
            )

        # 替換方法
        backtest_service._generate_signal = generate_signal_with_threshold

        # 運行回測
        result = backtest_service.run_backtest(base_config)
        results[threshold] = result

        # 恢復原始方法
        backtest_service._generate_signal = original_generate_signal

        # 顯示簡要結果
        print(f"\n📈 結果摘要（門檻 {threshold}）:")
        print(f"   總交易次數: {result.total_trades}")
        print(f"   獲利交易: {result.winning_trades}")
        print(f"   虧損交易: {result.losing_trades}")
        print(f"   勝率: {result.win_rate:.2f}%")
        print(f"   總盈虧: {result.total_pnl_twd:,.0f} TWD")
        print(f"   最大回撤: {result.max_drawdown:.2f}%")
        print(f"   盈虧比: {result.profit_factor:.2f}")

    # 比較結果
    print("\n" + "=" * 80)
    print("📊 不同門檻比較總結")
    print("=" * 80)
    print(
        f"\n{'門檻':<8} {'交易次數':<10} {'獲利/虧損':<12} {'勝率':<10} {'總盈虧':<15} {'最大回撤':<10} {'盈虧比':<8}"
    )
    print("-" * 80)

    for threshold in thresholds:
        result = results[threshold]
        print(
            f"{threshold:<8.1f} {result.total_trades:<10} "
            f"{result.winning_trades}/{result.losing_trades:<10} "
            f"{result.win_rate:<9.2f}% "
            f"{result.total_pnl_twd:<14,.0f} "
            f"{result.max_drawdown:<9.2f}% "
            f"{result.profit_factor:<8.2f}"
        )

    # 找出最佳門檻
    best_threshold = max(results.keys(), key=lambda t: results[t].total_pnl_twd)
    best_result = results[best_threshold]

    print("\n" + "=" * 80)
    print(f"🏆 最佳門檻: {best_threshold}")
    print(f"   總盈虧: {best_result.total_pnl_twd:,.0f} TWD")
    print(f"   勝率: {best_result.win_rate:.2f}%")
    print(f"   最大回撤: {best_result.max_drawdown:.2f}%")
    print("=" * 80)

    # 保存詳細報告
    for threshold in thresholds:
        result = results[threshold]
        suffix = f"golden_{threshold:.1f}".replace(".", "_")
        backtest_service.save_results(result, base_config, suffix=suffix)
        print(f"\n✅ 已保存門檻 {threshold} 的詳細報告")


if __name__ == "__main__":
    main()
