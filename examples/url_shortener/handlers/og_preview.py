"""Open Graph metadata fetching and caching.

Fetches OG tags (title, description, image) from the target URL
and caches them in the ShortLink record for instant preview serving.

Sig: 2026-05-08 created
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException, Query, Request

try:
    from generated.models import ShortLink
except ImportError:
    from models import ShortLink


def _extract_meta(soup: BeautifulSoup, prop: str) -> str | None:
    """Return the ``content`` attribute of ``<meta property=prop>`` or None.

    Sig: 2026-05-08 created
    """
    tag = soup.find("meta", attrs={"property": prop})
    if tag is None:
        return None
    content = tag.get("content")
    return content.strip() if isinstance(content, str) and content.strip() else None


async def fetch_og_metadata(
    alias: str,
    repo,
    request: Request = None,
    force_refresh: bool = Query(False),
) -> dict[str, Any]:
    """Fetch and cache Open Graph metadata for the link behind ``alias``.

    Args:
        alias: Short link identifier whose target URL should be inspected.
        repo: Repository exposing async ``get`` and ``update`` on ShortLink.
        request: Incoming FastAPI request (reserved for future base URL use).
        force_refresh: When True, bypass the cached OG fields and re-fetch.

    Returns:
        Dict matching the OGMetadata model with keys ``alias``, ``title``,
        ``description``, ``image``, ``fetched_at``. Fields may be None when
        upstream fetching fails or the target exposes no OG tags.

    Raises:
        HTTPException: 404 when the alias is not registered in storage.

    Side Effects:
        Updates the ShortLink record with ``og_title``/``og_description``/
        ``og_image`` on successful fetch to memoise future calls.

    Sig: 2026-05-08 created
    """
    link = await repo.get(ShortLink, alias)
    if link is None:
        raise HTTPException(status_code=404, detail="Short link not found")

    cached_title = getattr(link, "og_title", None)
    if cached_title is not None and not force_refresh:
        return {
            "alias": alias,
            "title": cached_title,
            "description": getattr(link, "og_description", None),
            "image": getattr(link, "og_image", None),
            "fetched_at": datetime.now(timezone.utc),
        }

    title: str | None = None
    description: str | None = None
    image: str | None = None

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(link.original_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            title = _extract_meta(soup, "og:title")
            description = _extract_meta(soup, "og:description")
            image = _extract_meta(soup, "og:image")

            if title is None and soup.title and soup.title.string:
                title = soup.title.string.strip() or None

            await repo.update(
                ShortLink,
                alias,
                {
                    "og_title": title,
                    "og_description": description,
                    "og_image": image,
                },
            )
    except (httpx.HTTPError, httpx.TimeoutException):
        # Upstream is unreachable or misbehaving; surface partial data so the
        # caller can decide whether to retry rather than masking as success.
        pass

    return {
        "alias": alias,
        "title": title,
        "description": description,
        "image": image,
        "fetched_at": datetime.now(timezone.utc),
    }
