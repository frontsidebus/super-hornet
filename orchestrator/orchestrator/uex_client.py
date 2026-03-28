"""Async client for UEX Corp API 2.0 — Star Citizen trade and economy data.

Provides access to commodity prices, trade routes, ship data, terminal info,
mining data, and location lookups via the UEX Corp public API.

API docs: https://uexcorp.space/api/2.0
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class UEXClient:
    """Async HTTP client for the UEX Corp API 2.0."""

    def __init__(
        self,
        base_url: str = "https://uexcorp.space/api/2.0",
        api_key: str = "",
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Return (and lazily create) the shared httpx client."""
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
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
        """Execute an HTTP request with error handling and rate-limit awareness.

        Returns the parsed JSON body on success, or an empty dict/list on failure.
        Never raises — all errors are logged and swallowed.
        """
        client = await self._get_client()
        try:
            resp = await client.request(method, path, params=params)

            # Rate-limit awareness
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining is not None:
                try:
                    if int(remaining) < 10:
                        logger.warning(
                            "UEX rate limit nearly exhausted: %s requests remaining",
                            remaining,
                        )
                except ValueError:
                    pass

            resp.raise_for_status()
            return resp.json()

        except httpx.HTTPStatusError as exc:
            logger.error(
                "UEX API HTTP %s for %s: %s",
                exc.response.status_code,
                path,
                exc.response.text[:200],
            )
        except httpx.TimeoutException:
            logger.error("UEX API request timed out for %s", path)
        except Exception:
            logger.exception("Unexpected error calling UEX API %s", path)

        return {}

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_commodities(self) -> list[dict[str, Any]]:
        """Fetch the full list of tradeable commodities.

        GET /commodities
        """
        data = await self._request("GET", "/commodities")
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []

    async def get_commodity_prices(
        self, commodity_code: str, location: str = ""
    ) -> list[dict[str, Any]]:
        """Fetch price entries for a specific commodity, optionally filtered by location.

        GET /commodities_prices?commodity_code=<code>[&location=<loc>]
        """
        params: dict[str, Any] = {"commodity_code": commodity_code}
        if location:
            params["location"] = location
        data = await self._request("GET", "/commodities_prices", params=params)
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []

    async def get_best_trade_route(
        self, origin: str, destination: str, cargo_scu: int = 100
    ) -> dict[str, Any]:
        """Find the best trade route between two locations.

        GET /commodities_routes?origin=<origin>&destination=<dest>&cargo_scu=<scu>
        """
        params: dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "cargo_scu": cargo_scu,
        }
        data = await self._request("GET", "/commodities_routes", params=params)
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data:
            return data[0]
        return {}

    async def get_ship_data(self, ship_name: str) -> dict[str, Any]:
        """Retrieve vehicle/ship information by name.

        GET /vehicles?name=<ship_name>
        """
        params: dict[str, Any] = {"name": ship_name}
        data = await self._request("GET", "/vehicles", params=params)
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data:
            return data[0]
        return {}

    async def get_terminals(self, location: str = "") -> list[dict[str, Any]]:
        """List trade terminals, optionally filtered by location.

        GET /terminals[?location=<loc>]
        """
        params: dict[str, Any] = {}
        if location:
            params["location"] = location
        data = await self._request("GET", "/terminals", params=params)
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []

    async def get_mining_data(self, mineral: str = "") -> list[dict[str, Any]]:
        """Fetch mining yield / mineral data.

        GET /mining[?mineral=<mineral>]
        """
        params: dict[str, Any] = {}
        if mineral:
            params["mineral"] = mineral
        data = await self._request("GET", "/mining", params=params)
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []

    async def get_locations(self, system: str = "") -> list[dict[str, Any]]:
        """Fetch known locations, optionally filtered by star system.

        GET /locations[?system=<system>]
        """
        params: dict[str, Any] = {}
        if system:
            params["system"] = system
        data = await self._request("GET", "/locations", params=params)
        if isinstance(data, list):
            return data
        return data.get("data", []) if isinstance(data, dict) else []

    async def lookup_commodity(
        self, commodity: str, location: str = ""
    ) -> dict[str, Any]:
        """Look up a commodity by name, returning info and prices."""
        # Try direct code lookup first
        prices = await self.get_commodity_prices(commodity, location=location)
        if prices:
            return {"commodity": commodity, "prices": prices, "location": location or "all"}

        # Fall back to listing all commodities and fuzzy matching
        all_commodities = await self.get_commodities()
        match = None
        commodity_lower = commodity.lower()
        for c in all_commodities:
            name = c.get("name", "")
            code = c.get("code", "")
            if name.lower() == commodity_lower or code.lower() == commodity_lower:
                match = c
                break
        if match is None:
            return {"error": f"Commodity '{commodity}' not found"}

        code = match.get("code", commodity)
        prices = await self.get_commodity_prices(code, location=location)
        return {"commodity": match, "prices": prices, "location": location or "all"}

    async def plan_trade_route(
        self, origin: str, destination: str, cargo_scu: int = 100
    ) -> dict[str, Any]:
        """Plan a trade route between two locations."""
        return await self.get_best_trade_route(origin, destination, cargo_scu)
