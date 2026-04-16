"""主程式入口 - 使用 TradingEngine 新架構.

用法：
    # 使用預設 strategy.yaml
    uv run main

    # 指定配置檔（跑 MACD 波段策略）
    uv run main --config strategy_macd.yaml

    # 指定配置檔（跑 MA 均線糾纏突破策略）
    uv run main --config strategy_ma.yaml

    # 指定配置檔（跑 ORB 日內策略）
    uv run main --config strategy_orb.yaml
"""

import argparse
import os

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.engines.trading_engine import TradingEngine
from auto_trade.executors.live_executor import LiveExecutor
from auto_trade.models.trading_unit import TradingUnit
from auto_trade.services.account_service import AccountService
from auto_trade.services.indicator_service import IndicatorService
from auto_trade.services.line_bot_service import LineBotService
from auto_trade.services.market_service import MarketService
from auto_trade.services.order_service import OrderService
from auto_trade.services.record_service import RecordService
from auto_trade.strategies import create_strategy


def main():
    """主程式入口"""
    parser = argparse.ArgumentParser(description="自動交易系統")
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="YAML 配置檔名，如 strategy_macd.yaml（預設: strategy.yaml）",
    )
    args = parser.parse_args()

    # 載入統一配置
    config = Config(config_file=args.config)
    print(config)

    # 建立 API 客戶端
    api_client = create_api_client(
        config.api_key,
        config.secret_key,
        config.ca_cert_path,
        config.ca_password,
        simulation=config.simulation,
    )

    # 建立基礎服務
    account_service = AccountService(api_client)
    market_service = MarketService(api_client)
    order_service = OrderService(api_client)
    indicator_service = IndicatorService()

    # 建立 Line Bot 服務（可選）
    line_bot_service = None
    if (
        os.environ.get("LINE_CHANNEL_ID")
        and os.environ.get("LINE_CHANNEL_SECRET")
        and os.environ.get("LINE_MESSAGING_API_TOKEN")
    ):
        line_bot_service = LineBotService(
            channel_id=os.environ.get("LINE_CHANNEL_ID"),
            channel_secret=os.environ.get("LINE_CHANNEL_SECRET"),
            messaging_api_token=os.environ.get("LINE_MESSAGING_API_TOKEN"),
        )
        print("✅ Line Bot 服務已啟用")

    # 根據 strategy_type 建立策略
    # 直接傳入 trading_config 的全部參數，策略用 **kwargs 忽略不認識的 key
    strategy = create_strategy(
        config.strategy_type, indicator_service, **config.trading_config
    )
    print(f"📋 策略類型: {config.strategy_type} → {strategy.name}")

    # 建立 TradingUnit（pm_config 直接從 Config 取得，不用手動建立）
    trading_unit = TradingUnit(
        name=f"{strategy.name} - {config.symbol}",
        strategy=strategy,
        pm_config=config.pm_config,
    )

    # 建立 Executor
    executor = LiveExecutor(order_service)

    # 建立 RecordService（用策略名稱區分檔案和 Google Sheets 標記）
    record_service = RecordService(strategy_name=config.strategy_name)

    # 建立 TradingEngine
    engine = TradingEngine(
        trading_unit=trading_unit,
        market_service=market_service,
        executor=executor,
        indicator_service=indicator_service,
        account_service=account_service,
        record_service=record_service,
        line_bot_service=line_bot_service,
    )

    # 設定交易參數
    engine.configure(
        symbol=config.symbol,
        sub_symbol=config.sub_symbol,
        signal_check_interval=config.signal_check_interval,
        config_file=config.config_file,
    )

    # 啟動交易引擎
    engine.run()


if __name__ == "__main__":
    main()
