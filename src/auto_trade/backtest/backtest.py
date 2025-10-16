#!/usr/bin/env python3
"""回測命令行工具"""

from datetime import datetime, timedelta

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.models.backtest import BacktestConfig
from auto_trade.services.backtest_service import BacktestService
from auto_trade.services.market_service import MarketService
from auto_trade.services.strategy_service import StrategyService


def main():
    """主程式"""
    print("🚀 自動交易回測工具")
    print("=" * 50)

    # 直接在代碼中設定要測試的商品
    symbol = "TXF"  # 商品代碼
    sub_symbol = "TXF202511"  # 子商品代碼
    days = 60  # 回測天數
    capital = 1000000  # 初始資金

    try:
        # 載入配置
        config = Config()

        # 建立API客戶端
        api_client = create_api_client(
            config.api_key,
            config.secret_key,
            config.ca_cert_path,
            config.ca_password,
            simulation=True,  # 回測使用模擬模式
        )

        # 建立服務
        market_service = MarketService(api_client)
        strategy_service = StrategyService()
        backtest_service = BacktestService(market_service, strategy_service)

        # 設定回測參數
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        backtest_config = BacktestConfig(
            symbol=symbol,
            sub_symbol=sub_symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=capital,
            order_quantity=1,
            stop_loss_points=80,
            start_trailing_stop_points=250,
            trailing_stop_points=250,
            take_profit_points=600,
            timeframe="30m",
            max_positions=1,
            enable_trailing_stop=True,
            enable_take_profit=True,
        )

        print("📊 回測設定:")
        print(f"   商品: {backtest_config.symbol} ({backtest_config.sub_symbol})")
        print(
            f"   期間: {backtest_config.start_date.strftime('%Y-%m-%d')} - {backtest_config.end_date.strftime('%Y-%m-%d')}"
        )
        print(f"   初始資金: {backtest_config.initial_capital:,.0f}")
        print(f"   停損: {backtest_config.stop_loss_points} 點")
        print(f"   獲利了結: {backtest_config.take_profit_points} 點")
        print()

        # 執行回測
        result = backtest_service.run_backtest(backtest_config)

        # 生成報告
        report = backtest_service.generate_report(result)
        print(report)

        # 保存結果
        filename = backtest_service.save_results(result)

        print(f"✅ 回測完成！結果已保存到: {filename}")

    except Exception as e:
        print(f"❌ 回測失敗: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
