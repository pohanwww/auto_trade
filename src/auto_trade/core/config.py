"""Configuration management."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)


class Config:
    """統一配置管理類別 - 整合環境變數和 YAML 配置"""

    def __init__(self):
        """初始化配置

        配置會從以下來源載入：
        1. 環境變數（.env）- API 金鑰等敏感資訊
        2. YAML 配置檔（config/strategies.yaml）- 交易策略和商品設定
        """
        # === 環境配置（從 .env 讀取）===
        # Shioaji API 設定
        self.api_key: str = os.environ.get("API_KEY", "test_key")
        self.secret_key: str = os.environ.get("SECRET_KEY", "test_secret")
        self.ca_cert_path: str = os.environ.get(
            "CA_CERT_PATH", "credentials/Sinopac.pfx"
        )
        self.ca_password: str = os.environ.get("CA_PASSWORD", "test_password")
        self.simulation: bool = os.environ.get("SIMULATION", "true").lower() == "true"

        # Google Sheets 設定（可選）
        self.google_credentials_path: str | None = os.environ.get(
            "GOOGLE_CREDENTIALS_PATH"
        )
        self.google_spreadsheet_name: str | None = os.environ.get(
            "GOOGLE_SPREADSHEET_NAME"
        )

        # === 交易策略配置（從 YAML 讀取）===
        self._load_trading_config()

    def _load_trading_config(self):
        """載入交易策略配置"""
        # 找到 config 資料夾路徑
        config_dir = Path(__file__).parent.parent.parent.parent / "config"
        strategies_file = config_dir / "strategy.yaml"

        # 載入完整配置
        with open(strategies_file, encoding="utf-8") as f:
            config_data = yaml.safe_load(f)

        # 取得啟用的策略名稱
        active_strategy = config_data.get("active_strategy", "default")

        # 取得所有可用的策略（排除 active_strategy 和 symbol）
        available_strategies = [
            k for k in config_data if k not in ["active_strategy", "symbol"]
        ]

        # 驗證策略是否存在
        if active_strategy not in available_strategies:
            raise ValueError(
                f"策略 '{active_strategy}' 不存在。可用策略: {available_strategies}"
            )

        # 載入啟用的策略配置
        strategy_data = config_data[active_strategy]

        # 商品設定
        symbol_config = config_data["symbol"]
        self.symbol: str = symbol_config["current"]
        self.sub_symbol: str = symbol_config["contract"]
        self.symbol_name: str = symbol_config.get("name", "")

        # 交易參數（已整合風險管理）
        trading = strategy_data["trading"]
        self.order_quantity: int = trading["order_quantity"]
        self.timeframe: str = trading["timeframe"]
        self.stop_loss_points: int = trading["stop_loss_points"]
        self.stop_loss_points_rate: float | None = trading.get("stop_loss_points_rate")
        self.start_trailing_stop_points: int = trading["start_trailing_stop_points"]
        self.trailing_stop_points: int = trading["trailing_stop_points"]
        self.take_profit_points: int = trading["take_profit_points"]
        # 新增的百分比變數（可選，如果設置則會覆蓋固定點數）
        self.trailing_stop_points_rate: float | None = trading.get(
            "trailing_stop_points_rate"
        )
        self.take_profit_points_rate: float | None = trading.get(
            "take_profit_points_rate"
        )

        # 檢測頻率
        monitoring = strategy_data["monitoring"]
        self.signal_check_interval: int = monitoring["signal_check_interval"]
        self.position_check_interval: int = monitoring["position_check_interval"]

        # 保存策略名稱和可用策略列表
        self.strategy_name: str = active_strategy
        self.available_strategies: list[str] = available_strategies

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return not self.simulation

    def get_trading_params(self) -> dict:
        """取得交易參數字典（用於傳遞給 TradingService）"""
        return {
            "symbol": self.symbol,
            "sub_symbol": self.sub_symbol,
            "timeframe": self.timeframe,
            "stop_loss_points": self.stop_loss_points,
            "stop_loss_points_rate": self.stop_loss_points_rate,
            "start_trailing_stop_points": self.start_trailing_stop_points,
            "trailing_stop_points": self.trailing_stop_points,
            "take_profit_points": self.take_profit_points,
            "trailing_stop_points_rate": self.trailing_stop_points_rate,
            "take_profit_points_rate": self.take_profit_points_rate,
            "order_quantity": self.order_quantity,
            "signal_check_interval": self.signal_check_interval,
            "position_check_interval": self.position_check_interval,
        }

    def __repr__(self) -> str:
        """返回配置摘要"""
        trailing_stop_display = (
            f"{self.trailing_stop_points_rate * 100}%"
            if self.trailing_stop_points_rate is not None
            else f"{self.trailing_stop_points}點"
        )
        take_profit_display = (
            f"{self.take_profit_points_rate * 100}%"
            if self.take_profit_points_rate is not None
            else f"{self.take_profit_points}點"
        )
        stop_loss_display = (
            f"{self.stop_loss_points_rate * 100}%"
            if self.stop_loss_points_rate is not None
            else f"{self.stop_loss_points}點"
        )
        return (
            f"Config(\n"
            f"  環境: {'prod' if self.is_production else 'simulation'}\n"
            f"  策略: {self.strategy_name}\n"
            f"  商品: {self.symbol_name} ({self.sub_symbol})\n"
            f"  停損: {stop_loss_display}\n"
            f"  移動停損: {trailing_stop_display}\n"
            f"  獲利了結: {take_profit_display}\n"
            f")"
        )


if __name__ == "__main__":
    config = Config()
    print(config.api_key)
    print(config.secret_key)
    print(config.ca_cert_path)
    print(config.ca_password)
    print(config.simulation)
    print(config.is_production)
