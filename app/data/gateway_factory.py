from app.config import settings
from app.data.gateway_base import Gateway


def get_gateway() -> Gateway:
    if settings.gateway_mode == "mt5":
        from app.data.mt5_gateway import MT5Gateway
        return MT5Gateway()
    if settings.gateway_mode == "mock":
        from app.data.mock_gateway import MockGateway
        return MockGateway()
    raise ValueError(f"Unknown GATEWAY_MODE: {settings.gateway_mode!r} (expected 'mock' or 'mt5')")
