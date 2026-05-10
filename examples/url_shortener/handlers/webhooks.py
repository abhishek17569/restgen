"""Webhook management and fire-and-forget delivery.

Stores webhook configurations in SQLite and delivers payloads
asynchronously via httpx when matching events occur. Delivery is
best-effort with no retry queue.

Sig: 2026-05-08 created
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
from fastapi import HTTPException

_DB_PATH = Path(__file__).parent.parent / "analytics.db"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS webhooks (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    event TEXT NOT NULL,
    alias_filter TEXT,
    created_at TEXT NOT NULL
)
"""

_CREATE_EVENT_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_webhooks_event ON webhooks(event)"
)


async def _get_db() -> aiosqlite.Connection:
    """Open a connection to the shared analytics.db and ensure schema exists.

    Returns:
        An open ``aiosqlite.Connection`` with the ``webhooks`` table
        guaranteed to exist. Caller is responsible for closing.

    Side Effects:
        Creates the ``webhooks`` table + indexes on first call.

    Sig: 2026-05-08 created
    """
    conn = await aiosqlite.connect(_DB_PATH)
    await conn.execute(_CREATE_TABLE_SQL)
    await conn.execute(_CREATE_EVENT_INDEX_SQL)
    await conn.commit()
    return conn


async def init_webhooks_db() -> None:
    """Create the webhooks table and indexes. Intended for app startup.

    Side Effects:
        Creates/opens the ``analytics.db`` SQLite file and materializes
        the ``webhooks`` table and ``idx_webhooks_event`` index.

    Sig: 2026-05-08 created
    """
    conn = await _get_db()
    await conn.close()


async def create_webhook(body: Any, repo: Any) -> dict[str, Any]:
    """Persist a new webhook configuration.

    Args:
        body: Request body with ``url``, ``event``, and optional
            ``alias_filter`` attributes.
        repo: Repository instance (unused; kept for handler signature
            parity with other handlers).

    Returns:
        Dict matching the ``WebhookResponse`` schema.

    Side Effects:
        Inserts a row into the ``webhooks`` SQLite table.

    Sig: 2026-05-08 created
    """
    webhook_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    alias_filter = getattr(body, "alias_filter", None)

    conn = await _get_db()
    try:
        await conn.execute(
            "INSERT INTO webhooks (id, url, event, alias_filter, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (webhook_id, body.url, body.event, alias_filter, created_at),
        )
        await conn.commit()
    finally:
        await conn.close()

    return {
        "id": webhook_id,
        "url": body.url,
        "event": body.event,
        "alias_filter": alias_filter,
        "created_at": created_at,
    }


async def list_webhooks(repo: Any) -> list[dict[str, Any]]:
    """Return every webhook configuration currently stored.

    Args:
        repo: Repository instance (unused; kept for handler signature
            parity with other handlers).

    Returns:
        List of dicts matching the ``WebhookResponse`` schema.

    Sig: 2026-05-08 created
    """
    conn = await _get_db()
    try:
        cursor = await conn.execute(
            "SELECT id, url, event, alias_filter, created_at FROM webhooks"
        )
        rows = await cursor.fetchall()
        await cursor.close()
    finally:
        await conn.close()

    return [
        {
            "id": row[0],
            "url": row[1],
            "event": row[2],
            "alias_filter": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]


async def delete_webhook(webhook_id: str, repo: Any) -> None:
    """Remove a webhook by id.

    Args:
        webhook_id: Hex uuid of the webhook to delete.
        repo: Repository instance (unused; kept for handler signature
            parity with other handlers).

    Raises:
        HTTPException: 404 if no webhook with that id exists.

    Side Effects:
        Deletes a row from the ``webhooks`` SQLite table.

    Sig: 2026-05-08 created
    """
    conn = await _get_db()
    try:
        cursor = await conn.execute(
            "DELETE FROM webhooks WHERE id = ?", (webhook_id,)
        )
        deleted = cursor.rowcount
        await conn.commit()
        await cursor.close()
    finally:
        await conn.close()

    if not deleted:
        raise HTTPException(status_code=404, detail="Webhook not found")


async def _deliver(url: str, payload: dict[str, Any]) -> None:
    """Best-effort single-shot POST. Swallows every failure.

    Args:
        url: Destination webhook URL.
        payload: JSON-serializable body.

    Side Effects:
        Performs a network POST. Failures are silently discarded since
        this function runs detached as a fire-and-forget task and a
        crash would terminate the event loop task without a handler.

    Sig: 2026-05-08 created
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json=payload)
    except Exception:
        # Best-effort delivery: no retry queue, no logging pipeline here.
        pass


async def fire_webhook(
    event: str,
    payload: dict[str, Any],
    alias: str | None = None,
) -> None:
    """Dispatch matching webhooks without waiting for their responses.

    When ``alias`` is provided, matches webhooks whose ``alias_filter``
    equals that alias OR is NULL (unfiltered subscribers). When ``alias``
    is None, matches only webhooks whose ``alias_filter`` is NULL.

    Args:
        event: Event name (e.g., "click", "link_created").
        payload: Extra fields merged into the delivered body.
        alias: Short-link alias associated with the event, if any.

    Side Effects:
        Schedules one detached ``asyncio.Task`` per matching webhook.
        Returns as soon as tasks are scheduled; does not await delivery.

    Sig: 2026-05-08 created
    """
    conn = await _get_db()
    try:
        if alias is not None:
            cursor = await conn.execute(
                "SELECT url FROM webhooks WHERE event = ?"
                " AND (alias_filter = ? OR alias_filter IS NULL)",
                (event, alias),
            )
        else:
            cursor = await conn.execute(
                "SELECT url FROM webhooks WHERE event = ?"
                " AND alias_filter IS NULL",
                (event,),
            )
        rows = await cursor.fetchall()
        await cursor.close()
    finally:
        await conn.close()

    if not rows:
        return

    body: dict[str, Any] = {
        "event": event,
        "alias": alias,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }

    for (url,) in rows:
        asyncio.create_task(_deliver(url, body))
