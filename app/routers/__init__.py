"""Router initialization"""

from .rebalancing import router as rebalancing_router
from .system import router as system_router
from .portfolio import router as portfolio_router
from .auth import router as auth_router
from .admin import router as admin_router

__all__ = ["rebalancing_router", "system_router", "portfolio_router", "auth_router", "admin_router"]
