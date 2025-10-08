"""Trading-related exceptions."""


class TradingError(Exception):
    """Base exception for trading operations."""

    pass


class OrderError(TradingError):
    """Exception raised for order-related errors."""

    pass


class MarketDataError(TradingError):
    """Exception raised for market data errors."""

    pass
