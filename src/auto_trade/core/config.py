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
        2. YAML 配置檔（config/strategy.yaml）- 交易策略和商品設定
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
        from auto_trade.services.position_manager import PositionManagerConfig

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

        # 策略類型（用於選擇策略 class）
        self.strategy_type: str = strategy_data.get(
            "strategy_type", "macd_golden_cross"
        )

        # 用 from_dict() 一行建立 PositionManagerConfig —— 新增參數時不用改這裡
        trading = strategy_data["trading"]
        position = strategy_data.get("position", {})
        self.pm_config: PositionManagerConfig = PositionManagerConfig.from_dict(
            trading, position
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

    def __repr__(self) -> str:
        """返回配置摘要"""
        pm = self.pm_config
        trailing_stop_display = (
            f"{pm.trailing_stop_points_rate * 100}%"
            if pm.trailing_stop_points_rate is not None
            else f"{pm.trailing_stop_points}點"
        )
        take_profit_display = (
            f"{pm.take_profit_points_rate * 100}%"
            if pm.take_profit_points_rate is not None
            else f"{pm.take_profit_points}點"
        )
        stop_loss_display = (
            f"{pm.stop_loss_points_rate * 100}%"
            if pm.stop_loss_points_rate is not None
            else f"{pm.stop_loss_points}點"
        )
        fs_display = "啟用" if pm.enable_macd_fast_stop else "禁用"
        tighten_display = ""
        if pm.has_tightened_trailing_stop:
            tighten_display = f"\n  收緊移停: 獲利 {pm.tighten_after_points}點後 → {pm.tightened_trailing_stop_points}點"
        return (
            f"Config(\n"
            f"  環境: {'prod' if self.is_production else 'simulation'}\n"
            f"  策略: {self.strategy_name} (類型: {self.strategy_type})\n"
            f"  商品: {self.symbol_name} ({self.sub_symbol})\n"
            f"  倉位: 總量 {pm.total_quantity} = TP×{pm.tp_leg_quantity} + TS×{pm.ts_leg_quantity}\n"
            f"  停損: {stop_loss_display}\n"
            f"  移動停損: {trailing_stop_display}\n"
            f"  獲利了結: {take_profit_display}\n"
            f"  快速停損 (FS): {fs_display}"
            f"{tighten_display}\n"
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
