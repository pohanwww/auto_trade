from auto_trade.core.client import create_api_client
from auto_trade.core.config import Config
from auto_trade.services.account_service import AccountService
from auto_trade.services.market_service import MarketService
from auto_trade.services.order_service import OrderService
from auto_trade.services.strategy_service import StrategyService
from auto_trade.services.trading_service import TradingService


def main():
    """主程式入口"""
    # 載入配置
    symbol = "MXF"
    sub_symbol = "MXF202511"  # 2025年11月合約
    config = Config()

    # 交易參數設定
    trading_params = {
        "start_trailing_stop_points": 250,  # 啟動移動停損的獲利點數
        "trailing_stop_points": 250,  # 移動停損點數
        "order_quantity": 1,  # 下單數量
        "stop_loss_points": 80,  # 初始停損點數
        "take_profit_points": 500,  # 獲利了結點數
        "signal_check_interval": 5,  # 訊號檢測間隔 (分鐘)
        "position_check_interval": 3,  # 持倉檢測間隔 (秒)
    }

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

    # 建立交易服務
    trading_service = TradingService(
        api_client, account_service, market_service, order_service, strategy_service
    )

    # 設定交易參數
    trading_service.set_trading_params(trading_params)

    # 執行策略循環
    trading_service.run_strategy(symbol, sub_symbol)


if __name__ == "__main__":
    main()
