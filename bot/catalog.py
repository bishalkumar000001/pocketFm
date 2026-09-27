from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote_plus, urljoin

import aiohttp
from bs4 import BeautifulSoup

BASE_URL = "https://pocketfm.com"
SEARCH_URL = BASE_URL + "/search"


class CatalogClient:
    """Public Pocket FM catalog reader.

    This adapter reads information exposed on Pocket FM's public web pages.
    It does not attempt to bypass login, coins, DRM, signed-media controls,
    or other access restrictions. An episode is downloadable only when the
    public page exposes a directly usable media URL.
    """

    def __init__(self, timeout: int):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._page_cache: dict[str, str] = {}
        self.headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PocketFMStoryBot/1.0)",
            "Accept-Language": "en-US,en;q=0.9",
        }

    async def _get(self, url: str) -> str:
        if url in self._page_cache:
            return self._page_cache[url]
        async with aiohttp.ClientSession(timeout=self.timeout, headers=self.headers) as session:
            async with session.get(url, allow_redirects=True) as response:
                response.raise_for_status()
                text = await response.text()
        # Cache public catalog pages during the process lifetime to avoid
        # repeatedly fetching the same story for every requested episode.
        if "/show/" in url:
            self._page_cache[url] = text
        return text

    @staticmethod
    def _clean(text: str | None) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    @staticmethod
    def _show_id(url: str) -> str | None:
        match = re.search(r"/show/([a-zA-Z0-9]+)", url)
        return match.group(1) if match else None

    def _parse_show_card(self, anchor) -> dict[str, Any] | None:
        href = anchor.get("href", "")
        if not href.startswith("/show/"):
            return None
        url = urljoin(BASE_URL, href.split("?")[0])
        story_id = self._show_id(url)
        if not story_id:
            return None

        title = self._clean(anchor.get_text(" ", strip=True))
        if not title:
            image = anchor.find("img")
            title = self._clean(image.get("alt") if image else "")
        if not title:
            return None

        image = anchor.find("img")
        poster = None
        if image:
            poster = image.get("src") or image.get("data-src") or image.get("data-lazy-src")
            if poster:
                poster = urljoin(BASE_URL, poster)

        return {
            "id": story_id,
            "title": title[:200],
            "description": "",
            "poster": poster,
            "episodes": None,
            "url": url,
            "metadata": {"source": "pocketfm.com/public-web"},
        }

    async def search(self, query: str) -> list[dict[str, Any]]:
        # Pocket FM's public search page is used instead of a private API key.
        html = await self._get(f"{SEARCH_URL}?q={quote_plus(query)}")
        soup = BeautifulSoup(html, "html.parser")

        found: dict[str, dict[str, Any]] = {}
        for anchor in soup.find_all("a", href=True):
            item = self._parse_show_card(anchor)
            if item:
                found[item["id"]] = item

        # Some deployments expose the search value as ?query= instead of ?q=.
        if not found:
            html = await self._get(f"{SEARCH_URL}?query={quote_plus(query)}")
            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.find_all("a", href=True):
                item = self._parse_show_card(anchor)
                if item:
                    found[item["id"]] = item

        # Final fallback: Pocket FM's public /show catalog contains many
        # discoverable stories even when the search page is client-rendered.
        if not found:
            html = await self._get(f"{BASE_URL}/show")
            soup = BeautifulSoup(html, "html.parser")
            wanted = query.lower().strip()
            for anchor in soup.find_all("a", href=True):
                item = self._parse_show_card(anchor)
                if item and wanted in item["title"].lower():
                    found[item["id"]] = item

        results = list(found.values())
        # Prefer titles containing the requested phrase while retaining useful
        # partial matches from the public search page.
        words = [w.lower() for w in query.split() if w.strip()]
        results.sort(key=lambda x: (sum(w in x["title"].lower() for w in words), x["title"].lower()), reverse=True)
        return results[:10]

    async def get_story(self, story: dict[str, Any]) -> dict[str, Any]:
        """Expand a search result using its public show page."""
        html = await self._get(story["url"])
        soup = BeautifulSoup(html, "html.parser")

        result = dict(story)
        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image and og_image.get("content"):
            result["poster"] = urljoin(BASE_URL, og_image["content"])

        title = soup.find("meta", attrs={"property": "og:title"})
        if title and title.get("content"):
            result["title"] = self._clean(title["content"]).replace(" | Audio Series on Pocket FM", "")

        description = soup.find("meta", attrs={"property": "og:description"})
        if description and description.get("content"):
            result["description"] = self._clean(description["content"])

        text = self._clean(soup.get_text(" ", strip=True))
        episodes = re.search(r"Episodes\s+(\d+)", text, re.I)
        if episodes:
            result["episodes"] = int(episodes.group(1))

        # Extract structured data when available.
        metadata: dict[str, Any] = dict(result.get("metadata") or {})
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                value = json.loads(script.string or script.get_text())
                if isinstance(value, dict):
                    metadata["jsonld"] = value
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        result["metadata"] = metadata
        return result

    @staticmethod
    def _episode_entries(soup: BeautifulSoup, story_url: str) -> dict[int, dict[str, Any]]:
        entries: dict[int, dict[str, Any]] = {}
        for anchor in soup.find_all("a", href=True):
            text = CatalogClient._clean(anchor.get_text(" ", strip=True))
            match = re.search(r"\bE(?:pisode\s*)?(\d+)\b[.:-]?\s*(.*)", text, re.I)
            if not match:
                continue
            number = int(match.group(1))
            title = CatalogClient._clean(match.group(2)) or f"Episode {number}"
            entries[number] = {"number": number, "title": title, "url": urljoin(BASE_URL, anchor["href"])}
        return entries

    @staticmethod
    def _public_media_urls(soup: BeautifulSoup) -> list[str]:
        urls: list[str] = []
        pattern = re.compile(r"https?://[^\"'<>\s]+\.(?:mp3|m4a|aac|ogg|wav|mp4|m3u8)(?:\?[^\"'<>\s]*)?", re.I)
        for script in soup.find_all("script"):
            text = script.string or script.get_text() or ""
            urls.extend(pattern.findall(text))
        for tag in soup.find_all(["audio", "source", "video"]):
            src = tag.get("src")
            if src and re.search(r"\.(?:mp3|m4a|aac|ogg|wav|mp4|m3u8)(?:\?|$)", src, re.I):
                urls.append(urljoin(BASE_URL, src))
        # Preserve order and remove duplicates.
        return list(dict.fromkeys(urls))

    async def resolve_episode(self, story: dict[str, Any], episode_number: int) -> dict[str, Any] | None:
        story = await self.get_story(story)
        html = await self._get(story["url"])
        soup = BeautifulSoup(html, "html.parser")
        entries = self._episode_entries(soup, story["url"])
        entry = entries.get(episode_number)
        if not entry:
            return None

        media_urls = self._public_media_urls(soup)
        # Only return media URLs that are explicitly exposed in the public page.
        # If Pocket FM does not expose a direct URL, caller reports it as unavailable.
        if not media_urls:
            return {"number": episode_number, "title": entry["title"], "url": None, "source_url": entry["url"]}

        return {
            "number": episode_number,
            "title": entry["title"],
            "url": media_urls[0],
            "source_url": entry["url"],
        }
