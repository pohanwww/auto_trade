import os

from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.services.account_service import AccountService
from auto_trade.services.line_bot_service import LineBotService
from auto_trade.services.market_service import MarketService
from auto_trade.services.order_service import OrderService
from auto_trade.services.strategy_service import StrategyService
from auto_trade.services.trading_service import TradingService


def main():
    """主程式入口"""
    # 載入統一配置（環境變數 + 交易策略）
    # 策略選擇在 config/strategies.yaml 的 active_strategy 中設定
    config = Config()

    # 顯示配置摘要
    print(config)

    # 建立API客戶端
    api_client = create_api_client(
        config.api_key,
        config.secret_key,
        config.ca_cert_path,
        config.ca_password,
        simulation=config.simulation,
    )

    # 建立服務
    account_service = AccountService(api_client)
    market_service = MarketService(api_client)
    order_service = OrderService(api_client)
    strategy_service = StrategyService()

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

    # 建立交易服務
    trading_service = TradingService(
        api_client,
        account_service,
        market_service,
        order_service,
        strategy_service,
        line_bot_service,
    )

    # 設定交易參數（從統一配置中取得）
    trading_service.set_trading_params(config.get_trading_params())

    # 發送啟動通知
    if line_bot_service:
        line_bot_service.send_status_message("交易系統已啟動")

    # 執行策略循環
    trading_service.run_strategy()


if __name__ == "__main__":
    main()
