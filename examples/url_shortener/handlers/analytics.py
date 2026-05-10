"""Click analytics with SQLite storage.

Stores per-click events (IP, UA, referrer, parsed device info) in SQLite
for detailed analytics queries. SQLite is chosen over Redis for event-log
data because it handles time-series aggregation natively.

Sig: 2026-05-08 created
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import HTTPException

try:
    from generated.models import ShortLink
except ImportError:
    from models import ShortLink


_DB_PATH = Path(__file__).parent.parent / "analytics.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS click_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alias TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    referrer TEXT,
    browser TEXT,
    os TEXT,
    device_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_click_events_alias_ts
    ON click_events (alias, timestamp);
"""

_BROWSERS = ("Edge", "Chrome", "Firefox", "Safari", "Opera")
_OS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Windows", r"Windows"),
    ("macOS", r"Mac OS X|macOS"),
    ("iOS", r"iPhone|iPad|iPod"),
    ("Android", r"Android"),
    ("Linux", r"Linux"),
)
_BOT_RE = re.compile(r"bot|crawl|spider", re.IGNORECASE)


async def _get_db() -> aiosqlite.Connection:
    """Open an aiosqlite connection, ensuring the schema exists.

    Returns:
        An open ``aiosqlite.Connection`` pointed at ``analytics.db``.

    Side Effects:
        Creates the database file and ``click_events`` table + index
        on first invocation.

    Sig: 2026-05-08 created
    """
    conn = await aiosqlite.connect(_DB_PATH)
    await conn.executescript(_SCHEMA)
    await conn.commit()
    return conn


def _parse_user_agent(ua_string: str) -> dict[str, str]:
    """Extract browser, OS, and device type from a User-Agent string.

    Uses lightweight regex matching instead of a full UA parsing library
    to keep the dependency surface small; accuracy is "good enough" for
    coarse analytics buckets.

    Args:
        ua_string: Raw ``User-Agent`` header value.

    Returns:
        Dict with keys ``browser``, ``os``, ``device_type``.

    Sig: 2026-05-08 created
    """
    ua = ua_string or ""

    # Edge must precede Chrome because Edge UA contains "Chrome"
    browser = "Other"
    for candidate in _BROWSERS:
        if candidate in ua:
            browser = candidate
            break
    # Safari only counts if no Chrome/Edge in UA (both advertise "Safari")
    if browser == "Safari" and ("Chrome" in ua or "Edg" in ua):
        browser = "Other"

    os_name = "Other"
    for name, pattern in _OS_PATTERNS:
        if re.search(pattern, ua):
            os_name = name
            break

    if _BOT_RE.search(ua):
        device_type = "bot"
    elif "iPad" in ua or "Tablet" in ua:
        device_type = "tablet"
    elif "Mobile" in ua or "Android" in ua or "iPhone" in ua:
        device_type = "mobile"
    else:
        device_type = "desktop"

    return {"browser": browser, "os": os_name, "device_type": device_type}


async def record_click(alias: str, request: Any) -> None:
    """Persist a single click event to SQLite.

    Args:
        alias: The short-link alias that was hit.
        request: Starlette/FastAPI ``Request`` object; used for client IP
            and inbound headers.

    Side Effects:
        Inserts one row into ``click_events``.

    Sig: 2026-05-08 created
    """
    ip_address = request.client.host if getattr(request, "client", None) else None
    ua_string = request.headers.get("user-agent", "")
    referrer = request.headers.get("referer")
    parsed = _parse_user_agent(ua_string)
    timestamp = datetime.now(timezone.utc).isoformat()

    conn = await _get_db()
    try:
        await conn.execute(
            """
            INSERT INTO click_events
                (alias, timestamp, ip_address, user_agent, referrer,
                 browser, os, device_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alias,
                timestamp,
                ip_address,
                ua_string,
                referrer,
                parsed["browser"],
                parsed["os"],
                parsed["device_type"],
            ),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_analytics(alias: str, repo: Any) -> dict[str, Any]:
    """Aggregate click_events for one alias into detailed analytics.

    Args:
        alias: The short-link alias to summarize.
        repo: Repository instance used to confirm the alias still exists
            in Redis before running SQLite aggregations.

    Returns:
        Dict matching the ``DetailedAnalytics`` response shape: totals
        plus per-browser / per-OS / per-device / per-referrer breakdowns
        and a 30-day daily click series.

    Raises:
        HTTPException: 404 if the alias is not present in Redis.

    Sig: 2026-05-08 created
    """
    existing = await repo.get(ShortLink, alias)
    if existing is None:
        raise HTTPException(status_code=404, detail="Short link not found")

    conn = await _get_db()
    try:
        conn.row_factory = aiosqlite.Row

        async with conn.execute(
            "SELECT COUNT(*) AS n FROM click_events WHERE alias = ?",
            (alias,),
        ) as cur:
            row = await cur.fetchone()
            total_clicks = int(row["n"]) if row else 0

        clicks_by_browser = await _group_count(
            conn, alias, "browser"
        )
        clicks_by_os = await _group_count(conn, alias, "os")
        clicks_by_device = await _group_count(conn, alias, "device_type")

        async with conn.execute(
            """
            SELECT COALESCE(referrer, '') AS key, COUNT(*) AS n
            FROM click_events
            WHERE alias = ?
            GROUP BY COALESCE(referrer, '')
            ORDER BY n DESC
            LIMIT 20
            """,
            (alias,),
        ) as cur:
            clicks_by_referrer = {r["key"]: int(r["n"]) async for r in cur}

        async with conn.execute(
            """
            SELECT substr(timestamp, 1, 10) AS date, COUNT(*) AS n
            FROM click_events
            WHERE alias = ?
              AND date(substr(timestamp, 1, 10))
                  >= date('now', '-30 days')
            GROUP BY substr(timestamp, 1, 10)
            ORDER BY date ASC
            """,
            (alias,),
        ) as cur:
            daily_clicks = [
                {"date": r["date"], "count": int(r["n"])} async for r in cur
            ]
    finally:
        await conn.close()

    return {
        "alias": alias,
        "total_clicks": total_clicks,
        "clicks_by_browser": clicks_by_browser,
        "clicks_by_os": clicks_by_os,
        "clicks_by_device": clicks_by_device,
        "clicks_by_referrer": clicks_by_referrer,
        "daily_clicks": daily_clicks,
    }


async def _group_count(
    conn: aiosqlite.Connection,
    alias: str,
    column: str,
) -> dict[str, int]:
    """Return a ``{value: count}`` map for GROUP BY on a whitelisted column.

    The column name is interpolated into SQL, so only pass trusted literals
    from inside this module — never user input.

    Sig: 2026-05-08 created
    """
    # Whitelist guard: only columns we know are safe to interpolate.
    if column not in {"browser", "os", "device_type"}:
        raise ValueError(f"refusing to group by untrusted column: {column!r}")

    query = (
        f"SELECT COALESCE({column}, '') AS key, COUNT(*) AS n "
        "FROM click_events WHERE alias = ? "
        f"GROUP BY COALESCE({column}, '')"
    )
    async with conn.execute(query, (alias,)) as cur:
        return {r["key"]: int(r["n"]) async for r in cur}


async def init_analytics_db() -> None:
    """Create the ``click_events`` table and index if missing.

    Intended to be called once during FastAPI app startup.

    Side Effects:
        Creates / opens ``analytics.db`` on disk.

    Sig: 2026-05-08 created
    """
    conn = await aiosqlite.connect(_DB_PATH)
    try:
        await conn.executescript(_SCHEMA)
        await conn.commit()
    finally:
        await conn.close()
