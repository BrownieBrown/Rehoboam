"""Shared dependencies for the API"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any

from rehoboam.api import KickbaseAPI
from rehoboam.config import Settings, get_settings

# Thread pool for running sync code
_executor = ThreadPoolExecutor(max_workers=4)


async def run_sync(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a synchronous function in a thread pool"""
    loop = asyncio.get_event_loop()
    if kwargs:
        return await loop.run_in_executor(_executor, lambda: func(*args, **kwargs))
    return await loop.run_in_executor(_executor, func, *args)


@lru_cache
def get_cached_settings() -> Settings:
    """Get cached application settings"""
    return get_settings()


# Store for authenticated API instances (keyed by email)
_api_cache: dict[str, KickbaseAPI] = {}


def get_api_for_user(email: str, password: str) -> KickbaseAPI:
    """Get or create an authenticated KickbaseAPI instance"""
    if email not in _api_cache:
        api = KickbaseAPI(email=email, password=password)
        api.login()
        _api_cache[email] = api
    return _api_cache[email]


def clear_api_cache(email: str | None = None) -> None:
    """Clear cached API instances"""
    if email:
        _api_cache.pop(email, None)
    else:
        _api_cache.clear()


def get_cached_api(email: str) -> KickbaseAPI:
    """Get cached API instance for user (must have logged in first).

    Args:
        email: The user's email address from their JWT token

    Returns:
        The cached KickbaseAPI instance

    Raises:
        HTTPException: 401 if user's session not found in cache
    """
    from fastapi import HTTPException

    if email not in _api_cache:
        raise HTTPException(status_code=401, detail="Session expired, please login again")
    return _api_cache[email]
