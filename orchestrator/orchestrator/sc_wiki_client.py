"""Async client for the Star Citizen Wiki API.

Provides lookups for vehicles, items, star systems, manufacturers,
and general search against the community wiki API.

API docs: https://api.star-citizen.wiki
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SCWikiClient:
    """Async HTTP client for the Star Citizen Wiki API."""

    def __init__(
        self,
        base_url: str = "https://api.star-citizen.wiki",
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Return (and lazily create) the shared httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Execute an HTTP request with error handling.

        Returns parsed JSON on success, or an empty dict on failure.
        Never raises — all errors are logged and swallowed.
        """
        client = await self._get_client()
        try:
            resp = await client.request(method, path, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "SC Wiki API HTTP %s for %s: %s",
                exc.response.status_code,
                path,
                exc.response.text[:200],
            )
        except httpx.TimeoutException:
            logger.error("SC Wiki API request timed out for %s", path)
        except Exception:
            logger.exception("Unexpected error calling SC Wiki API %s", path)

        return {}

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search the wiki for articles matching *query*.

        Returns up to *limit* results.
        """
        params: dict[str, Any] = {"query": query, "limit": limit}
        data = await self._request("GET", "/api/v2/search", params=params)
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []

    async def get_vehicle(self, name: str) -> dict[str, Any]:
        """Fetch detailed vehicle/ship data by name.

        GET /api/v2/vehicles/{name}
        """
        data = await self._request("GET", f"/api/v2/vehicles/{name}")
        if isinstance(data, dict):
            return data
        return {}

    async def get_item(self, name: str) -> dict[str, Any]:
        """Fetch item data by name.

        GET /api/v2/items/{name}
        """
        data = await self._request("GET", f"/api/v2/items/{name}")
        if isinstance(data, dict):
            return data
        return {}

    async def get_star_system(self, name: str) -> dict[str, Any]:
        """Fetch star system information by name.

        GET /api/v2/starsystems/{name}
        """
        data = await self._request("GET", f"/api/v2/starsystems/{name}")
        if isinstance(data, dict):
            return data
        return {}

    async def get_manufacturers(self) -> list[dict[str, Any]]:
        """Fetch the list of all known ship/item manufacturers.

        GET /api/v2/manufacturers
        """
        data = await self._request("GET", "/api/v2/manufacturers")
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []
