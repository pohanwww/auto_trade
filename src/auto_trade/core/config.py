"""Configuration management."""

import os

from dotenv import load_dotenv

load_dotenv(override=True)


class Config:
    """Configuration class for auto trading system."""

    def __init__(self):
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

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return not self.simulation


if __name__ == "__main__":
    config = Config()
    print(config.api_key)
    print(config.secret_key)
    print(config.ca_cert_path)
    print(config.ca_password)
    print(config.simulation)
    print(config.is_production)
