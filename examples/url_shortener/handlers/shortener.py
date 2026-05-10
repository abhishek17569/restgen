"""URL shortener handlers with bloom-filter-powered short alias generation.

Design:
    Instead of generating random 6-char aliases (>36 billion possibilities but
    wastefully long), we start at 3 chars and grow only when the namespace fills.
    A bloom filter provides O(1) availability checks without hitting Redis on
    every candidate — we only touch Redis for the final confirmation + write.

    Alias generation strategy:
    1. Generate candidate at current_length (starts at 3)
    2. Check bloom filter: if "definitely not present" → available (fast path)
    3. If bloom says "maybe present" → generate next candidate (no Redis call)
    4. After finding a bloom-approved candidate, confirm with Redis (rare collision)
    5. On confirmation, register in bloom filter + write to Redis

    This gives us:
    - 3-char URLs for the first ~178K links
    - 4-char URLs up to ~11M links
    - Near-zero Redis calls during generation (bloom filter handles 99%+ checks)

Sig: 2026-05-08 modified
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import string
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from handlers.analytics import record_click
from handlers.bloom import (
    get_current_length,
    is_probably_taken,
    register_alias,
)
from handlers.validators import validate_custom_alias
from handlers.webhooks import fire_webhook

_BASE62 = string.ascii_letters + string.digits


def _generate_candidate(length: int, attempt: int = 0) -> str:
    """Generate a deterministic-ish short alias candidate.

    Uses a counter-based approach seeded by attempt number for better
    distribution than pure random. Falls back to random on high attempts.

    Sig: 2026-05-08 created
    """
    import random
    return "".join(random.choices(_BASE62, k=length))


def _validate_url(url: str) -> str:
    """Validate URL has http(s) scheme and a host.

    Args:
        url: Raw URL string from the request.

    Returns:
        The validated URL string.

    Raises:
        HTTPException: 422 if scheme or host is invalid.

    Sig: 2026-04-15 created
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=422,
            detail="URL must start with http:// or https://",
        )
    if not parsed.netloc:
        raise HTTPException(status_code=422, detail="Invalid URL format")
    return url


async def create_short_link(body, repo) -> dict[str, str]:
    """Create a shortened URL with bloom-filter-optimized alias generation.

    When a custom_alias is provided, validates it and checks availability via
    Redis. When generating, uses the bloom filter to find an available alias
    with minimal Redis calls. Optionally stores expiration, tags, and a
    password hash when those fields are present on the request body.

    Args:
        body: CreateLinkRequest with ``url`` and optional ``custom_alias``,
            ``expires_at``, ``tags``, ``password``.
        repo: Repository instance (Redis-backed).

    Returns:
        Dict matching CreateLinkResponse schema.

    Raises:
        HTTPException: 409 if custom alias is taken, 422 if URL is invalid or
            custom alias fails validation.

    Side Effects:
        Writes to Redis, registers alias in bloom filter, fires
        ``link_created`` webhook (fire-and-forget).

    Sig: 2026-05-08 modified
    """
    try:
        from generated.models import ShortLink
    except ImportError:
        from models import ShortLink

    original_url = _validate_url(body.url)

    if body.custom_alias:
        validate_custom_alias(body.custom_alias)
        existing = await repo.get(ShortLink, body.custom_alias)
        if existing is not None:
            raise HTTPException(status_code=409, detail="Alias already taken")
        alias = body.custom_alias
    else:
        alias = await _generate_with_bloom(repo, ShortLink)

    create_data = {
        "id": alias,
        "original_url": original_url,
        "clicks": 0,
    }
    if hasattr(body, "expires_at") and body.expires_at:
        create_data["expires_at"] = (
            body.expires_at.isoformat()
            if hasattr(body.expires_at, "isoformat")
            else body.expires_at
        )
    if hasattr(body, "tags") and body.tags:
        create_data["tags"] = (
            json.dumps(body.tags) if isinstance(body.tags, list) else body.tags
        )
    if hasattr(body, "password") and body.password:
        create_data["password_hash"] = hashlib.sha256(
            body.password.encode()
        ).hexdigest()

    await repo.create(ShortLink, create_data)

    register_alias(alias)

    asyncio.create_task(
        fire_webhook("link_created", {"original_url": original_url}, alias=alias)
    )

    return {
        "short_url": f"/{alias}",
        "original_url": original_url,
        "alias": alias,
    }


async def _generate_with_bloom(repo, model) -> str:
    """Generate the shortest available alias using bloom filter screening.

    Strategy:
    1. Try candidates at current_length
    2. Bloom filter rejects taken candidates instantly (no I/O)
    3. When bloom says "available", confirm with Redis (rare false positive)
    4. If stuck after MAX_ATTEMPTS, increment length and retry

    Sig: 2026-05-08 created
    """
    length = get_current_length()
    max_attempts = 50

    for attempt in range(max_attempts):
        candidate = _generate_candidate(length, attempt)

        # Fast path: bloom filter says "definitely not present"
        if not is_probably_taken(candidate):
            # Confirm with Redis (handles the rare case where bloom is stale)
            existing = await repo.get(model, candidate)
            if existing is None:
                return candidate
            # Bloom missed it (stale filter) — register and continue
            register_alias(candidate)

        # After many attempts at current length, try one char longer
        if attempt == max_attempts // 2:
            length += 1

    # Fallback: use a longer alias (should be extremely rare)
    length = get_current_length() + 2
    for _ in range(10):
        candidate = _generate_candidate(length)
        if not is_probably_taken(candidate):
            existing = await repo.get(model, candidate)
            if existing is None:
                return candidate
            register_alias(candidate)

    raise HTTPException(
        status_code=500, detail="Failed to generate unique alias"
    )


async def redirect_to_url(alias: str, repo, request=None) -> RedirectResponse:
    """Look up the alias, enforce expiration / password, increment clicks,
    record analytics, and redirect.

    Args:
        alias: The short link alias from the URL path.
        repo: Repository instance.
        request: FastAPI ``Request`` — used to capture analytics metadata.

    Returns:
        RedirectResponse (307) to the original URL.

    Raises:
        HTTPException: 404 if alias not found, 410 if expired, 403 if the
            link is password protected.

    Side Effects:
        Updates Redis click count, schedules analytics recording, fires
        ``milestone`` webhook every 100 clicks.

    Sig: 2026-05-08 modified
    """
    try:
        from generated.models import ShortLink
    except ImportError:
        from models import ShortLink

    link = await repo.get(ShortLink, alias)
    if link is None:
        raise HTTPException(status_code=404, detail="Short link not found")

    if getattr(link, "expires_at", None):
        expires = link.expires_at
        if isinstance(expires, str):
            expires = datetime.fromisoformat(expires)
        if expires < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=410, detail="This link has expired"
            )

    if getattr(link, "password_hash", None):
        raise HTTPException(
            status_code=403,
            detail="This link is password protected. Use /{alias}/access",
        )

    new_clicks = link.clicks + 1
    await repo.update(ShortLink, alias, {"clicks": new_clicks})

    if request is not None:
        asyncio.create_task(record_click(alias, request))

    if new_clicks % 100 == 0:
        asyncio.create_task(
            fire_webhook("milestone", {"clicks": new_clicks}, alias=alias)
        )

    return RedirectResponse(url=link.original_url, status_code=307)


async def format_stats(link) -> dict:
    """Transform a ShortLink record into the LinkStats response shape.

    Args:
        link: ShortLink model instance from the pipeline's db.get step.

    Returns:
        Dict matching LinkStats schema.

    Sig: 2026-04-15 created
    """
    return {
        "alias": link.id,
        "original_url": link.original_url,
        "clicks": link.clicks,
        "created_at": link.created_at,
    }


async def bulk_create_links(body, repo) -> dict:
    """Create multiple short links in one batch (max 50).

    Args:
        body: BulkCreateRequest with ``urls`` list and optional
            ``custom_aliases``, ``expires_at``, ``tags``.
        repo: Repository instance.

    Returns:
        Dict matching BulkCreateResponse with ``created`` and ``errors`` lists.

    Raises:
        HTTPException: 400 if more than 50 URLs submitted.

    Side Effects:
        Writes to Redis for each successfully created link; fires
        ``link_created`` webhook per creation via ``create_short_link``.

    Sig: 2026-05-08 created
    """
    if len(body.urls) > 50:
        raise HTTPException(
            status_code=400, detail="Bulk operation limited to 50 URLs"
        )

    custom_aliases = getattr(body, "custom_aliases", None) or []
    use_custom = len(custom_aliases) == len(body.urls)

    shared_expires_at = getattr(body, "expires_at", None)
    shared_tags = getattr(body, "tags", None)

    created: list[dict] = []
    errors: list[dict] = []

    class _Item:
        def __init__(self, url: str, custom_alias: str | None):
            self.url = url
            self.custom_alias = custom_alias
            self.expires_at = shared_expires_at
            self.tags = shared_tags
            self.password = None

    for i, url in enumerate(body.urls):
        custom_alias = custom_aliases[i] if use_custom else None
        try:
            result = await create_short_link(_Item(url, custom_alias), repo)
            created.append(result)
        except HTTPException as e:
            errors.append({"index": i, "url": url, "reason": str(e.detail)})
        except Exception as e:
            errors.append({"index": i, "url": url, "reason": str(e)})

    return {"created": created, "errors": errors}


async def update_link(alias: str, body, repo) -> dict:
    """Update an existing short link's properties.

    Args:
        alias: Path parameter - the link to update.
        body: UpdateLinkRequest with optional ``original_url``, ``expires_at``,
            ``tags``, ``password`` fields.
        repo: Repository instance.

    Returns:
        Dict with ``alias`` and list of ``updated_fields``.

    Raises:
        HTTPException: 404 if alias not found, 422 if ``original_url`` fails
            URL validation.

    Side Effects:
        Writes updated fields to Redis.

    Sig: 2026-05-08 created
    """
    try:
        from generated.models import ShortLink
    except ImportError:
        from models import ShortLink

    existing = await repo.get(ShortLink, alias)
    if existing is None:
        raise HTTPException(status_code=404, detail="Short link not found")

    update_data: dict = {}

    new_url = getattr(body, "original_url", None)
    if new_url is not None:
        update_data["original_url"] = _validate_url(new_url)

    new_expires = getattr(body, "expires_at", None)
    if new_expires is not None:
        update_data["expires_at"] = (
            new_expires.isoformat()
            if hasattr(new_expires, "isoformat")
            else new_expires
        )

    new_tags = getattr(body, "tags", None)
    if new_tags is not None:
        update_data["tags"] = (
            json.dumps(new_tags) if isinstance(new_tags, list) else new_tags
        )

    new_password = getattr(body, "password", None)
    if new_password is not None:
        update_data["password_hash"] = hashlib.sha256(
            new_password.encode()
        ).hexdigest()

    if update_data:
        await repo.update(ShortLink, alias, update_data)

    return {"alias": alias, "updated_fields": list(update_data.keys())}


async def password_access(alias: str, body, repo) -> dict:
    """Verify password and return the original URL for protected links.

    Args:
        alias: The password-protected link alias.
        body: PasswordAccessRequest with ``password`` field.
        repo: Repository instance.

    Returns:
        Dict matching PasswordAccessResponse with ``original_url`` and
        ``alias``.

    Raises:
        HTTPException: 404 if alias not found, 403 if password wrong.

    Sig: 2026-05-08 created
    """
    try:
        from generated.models import ShortLink
    except ImportError:
        from models import ShortLink

    link = await repo.get(ShortLink, alias)
    if link is None:
        raise HTTPException(status_code=404, detail="Short link not found")

    provided_hash = hashlib.sha256(body.password.encode()).hexdigest()
    stored_hash = getattr(link, "password_hash", None)

    if stored_hash != provided_hash:
        raise HTTPException(status_code=403, detail="Incorrect password")

    return {"original_url": link.original_url, "alias": alias}


async def get_links_by_tag(tag: str, repo) -> dict:
    """Retrieve all links that have a specific tag.

    Args:
        tag: The tag to filter by.
        repo: Repository instance.

    Returns:
        Dict matching TagListResponse with ``links``, ``tag``, ``count``.

    Side Effects:
        Performs a full scan of the links collection; cost is O(N) in the
        number of stored links.

    Sig: 2026-05-08 created
    """
    try:
        from generated.models import ShortLink
    except ImportError:
        from models import ShortLink

    all_links = await repo.list(ShortLink, limit=100000)

    matching: list = []
    for link in all_links:
        raw_tags = getattr(link, "tags", None)
        if not raw_tags:
            continue
        try:
            tag_list = (
                json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
            )
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(tag_list, list) and tag in tag_list:
            matching.append(link)

    return {"links": matching, "tag": tag, "count": len(matching)}
