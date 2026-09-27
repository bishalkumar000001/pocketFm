from typing import Any
import aiohttp


class CatalogClient:
    """Adapter for a documented/public/authorized catalog service.

    Set CATALOG_API_URL to your approved data provider. The bot does not
    attempt to bypass authentication, DRM, paywalls, or access controls.
    """

    def __init__(self, base_url: str, api_key: str, timeout: int):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def search(self, query: str) -> list[dict[str, Any]]:
        if not self.base_url:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with aiohttp.ClientSession(timeout=self.timeout, headers=headers) as session:
            async with session.get(f"{self.base_url}/search", params={"q": query}) as response:
                response.raise_for_status()
                data = await response.json()
        return data if isinstance(data, list) else data.get("results", [])

    async def resolve_episode(self, story: dict[str, Any], episode_number: int) -> dict[str, Any] | None:
        if not self.base_url:
            return None
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with aiohttp.ClientSession(timeout=self.timeout, headers=headers) as session:
            async with session.get(
                f"{self.base_url}/episode",
                params={"story_id": story["id"], "episode": episode_number},
            ) as response:
                if response.status == 404:
                    return None
                response.raise_for_status()
                return await response.json()
