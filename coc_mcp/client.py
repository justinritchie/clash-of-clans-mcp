"""HTTP client for the Clash of Clans API.

Docs: https://developer.clashofclans.com/#/documentation
Base URL: https://api.clashofclans.com/v1
Auth: Bearer token (IP-whitelisted)
Tag URL-encoding: '#' must be encoded as '%23'.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

API_BASE_URL = "https://api.clashofclans.com/v1"
DEFAULT_TIMEOUT = 30.0


def normalize_tag(tag: str) -> str:
    """Normalize a clan/player tag.

    Accepts '#YV9JRULU', 'YV9JRULU', '%23YV9JRULU', or 'yv9jrulu'.
    Returns the canonical form with leading '#', uppercase.
    """
    if not tag:
        raise ValueError("Tag cannot be empty.")
    cleaned = tag.strip().upper().replace("%23", "#")
    if not cleaned.startswith("#"):
        cleaned = "#" + cleaned
    return cleaned


def encode_tag(tag: str) -> str:
    """URL-encode a tag for path interpolation. '#' -> '%23'."""
    return quote(normalize_tag(tag), safe="")


class CocApiError(Exception):
    """Raised on COC API errors. Carries the HTTP status and friendly message."""

    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class CocClient:
    """Thin async wrapper around the Clash of Clans REST API."""

    def __init__(
        self,
        token: str,
        base_url: str = API_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not token:
            raise ValueError("CocClient requires an API token.")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "User-Agent": "coc-mcp/0.1.0",
        }

    async def _request(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        retries: int = 2,
    ) -> Dict[str, Any]:
        """GET request with simple retry on 429/5xx."""
        url = f"{self._base_url}{path}"
        backoff = 1.0
        last_exc: Optional[Exception] = None

        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url, params=params, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                self._raise_for_status(resp)
            except httpx.HTTPError as e:
                last_exc = e
                if attempt < retries:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                raise CocApiError(0, f"Network error: {e}") from e

        # Should not reach here; satisfy type checker.
        raise CocApiError(0, f"Request failed after {retries + 1} attempts: {last_exc}")

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        message_map = {
            400: "Bad request — check the tag format and parameters.",
            403: (
                "Access denied. Most common cause: your token's whitelisted IP "
                "doesn't match the IP making this request. Update the token at "
                "developer.clashofclans.com or regenerate one for your current IP."
            ),
            404: "Not found — the clan/player/war doesn't exist or is private.",
            429: "Rate limit exceeded. Slow down and retry shortly.",
            503: "Service unavailable. The COC API is temporarily down (maintenance?).",
        }
        msg = message_map.get(resp.status_code, f"HTTP {resp.status_code} from COC API.")
        raise CocApiError(resp.status_code, msg, body=body)

    # --- API endpoints -----------------------------------------------------

    async def get_clan(self, clan_tag: str) -> Dict[str, Any]:
        return await self._request(f"/clans/{encode_tag(clan_tag)}")

    async def get_clan_members(self, clan_tag: str) -> Dict[str, Any]:
        return await self._request(f"/clans/{encode_tag(clan_tag)}/members")

    async def get_warlog(self, clan_tag: str, limit: int = 10) -> Dict[str, Any]:
        return await self._request(
            f"/clans/{encode_tag(clan_tag)}/warlog",
            params={"limit": limit},
        )

    async def get_current_war(self, clan_tag: str) -> Dict[str, Any]:
        return await self._request(f"/clans/{encode_tag(clan_tag)}/currentwar")

    async def get_cwl_group(self, clan_tag: str) -> Dict[str, Any]:
        return await self._request(f"/clans/{encode_tag(clan_tag)}/currentwar/leaguegroup")

    async def get_cwl_war(self, war_tag: str) -> Dict[str, Any]:
        return await self._request(f"/clanwarleagues/wars/{encode_tag(war_tag)}")

    async def get_player(self, player_tag: str) -> Dict[str, Any]:
        return await self._request(f"/players/{encode_tag(player_tag)}")

    async def raw_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generic GET passthrough for endpoints not wrapped by a dedicated method.

        The path should start with '/' and follow the official API docs at
        https://developer.clashofclans.com/#/documentation. Tags inside the path
        must already be URL-encoded ('#' -> '%23'). Use encode_tag() helper if needed.
        """
        if not path.startswith("/"):
            path = "/" + path
        return await self._request(path, params=params)
