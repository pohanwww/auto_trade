"""Core functionality for auto trading system."""

from .client import create_api_client, with_api_client

__all__ = ["create_api_client", "with_api_client"]
