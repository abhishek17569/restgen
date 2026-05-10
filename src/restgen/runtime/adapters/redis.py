"""Redis repository adapter.

Uses Redis hashes for record storage. Each model gets a namespace:
  - Record data: key ``{prefix}:{model_name}:{id}`` with JSON-serialized fields
  - Index set: sorted set at key ``{prefix}:{model_name}:__index__`` tracking all IDs
  - Counter: key ``{prefix}:{model_name}:__counter__`` for insertion ordering

Gives O(1) get/create/update/delete and O(n) list with pagination via ZRANGE.

Sig: 2026-04-15 created
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, TypeVar
from uuid import uuid4

from ..repository import Repository

T = TypeVar("T")


class RedisRepository(Repository):
    """Redis-backed repository using JSON strings and sorted sets.

    Args:
        url: Redis connection URL (e.g., ``redis://localhost:6379/0``).
        prefix: Key prefix for namespacing (default: ``restgen``).

    Sig: 2026-04-15 created
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        prefix: str = "restgen",
    ) -> None:
        self._url = url
        self._prefix = prefix
        self._client: Any = None

    # -- Key helpers -----------------------------------------------------------

    def _key(self, model: type, id: Any) -> str:
        """Build Redis key for a record. Sig: 2026-04-15 created"""
        return f"{self._prefix}:{model.__name__}:{id}"

    def _index_key(self, model: type) -> str:
        """Build Redis key for the model's ID index. Sig: 2026-04-15 created"""
        return f"{self._prefix}:{model.__name__}:__index__"

    def _counter_key(self, model: type) -> str:
        """Build Redis key for the insertion counter. Sig: 2026-04-15 created"""
        return f"{self._prefix}:{model.__name__}:__counter__"

    def _get_pk_field(self, model: type) -> str:
        """Find primary key field name. Sig: 2026-04-15 created"""
        if hasattr(model, "model_fields"):
            if "id" in model.model_fields:
                return "id"
            return next(iter(model.model_fields))
        return "id"

    # -- Serialization ---------------------------------------------------------

    @staticmethod
    def _serialize(data: dict[str, Any]) -> str:
        """JSON-serialize a record dict, handling datetime/UUID.

        Sig: 2026-04-15 created
        """
        def default(obj: Any) -> str:
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            if hasattr(obj, "hex"):  # UUID
                return str(obj)
            return str(obj)

        return json.dumps(data, default=default)

    @staticmethod
    def _deserialize(raw: str) -> dict[str, Any]:
        """Deserialize a JSON string back to dict. Sig: 2026-04-15 created"""
        return json.loads(raw)

    # -- Lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to Redis. Sig: 2026-04-15 created"""
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(self._url, decode_responses=True)
        await self._client.ping()

    async def disconnect(self) -> None:
        """Disconnect from Redis. Sig: 2026-04-15 created"""
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- CRUD ------------------------------------------------------------------

    async def get(self, model: type[T], id: Any) -> T | None:
        """Fetch record by ID from Redis. Sig: 2026-04-15 created"""
        raw = await self._client.get(self._key(model, id))
        if raw is None:
            return None
        return model.model_validate(self._deserialize(raw))

    async def list(
        self,
        model: type[T],
        *,
        skip: int = 0,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
    ) -> list[T]:
        """List records with pagination and optional filters.

        Uses ZRANGE on the index sorted set for ordering, then fetches each
        record individually. Filters are applied in-memory after fetch.

        Sig: 2026-04-15 created
        """
        index_key = self._index_key(model)
        ids = await self._client.zrange(index_key, skip, skip + limit - 1)

        results: list[T] = []
        for record_id in ids:
            raw = await self._client.get(self._key(model, record_id))
            if raw is None:
                continue
            data = self._deserialize(raw)
            if filters and not all(
                data.get(k) == v for k, v in filters.items() if v is not None
            ):
                continue
            results.append(model.model_validate(data))
        return results

    async def create(self, model: type[T], data: dict[str, Any]) -> T:
        """Create a record in Redis. Sig: 2026-04-15 created"""
        pk_field = self._get_pk_field(model)

        if pk_field not in data or data[pk_field] is None:
            data[pk_field] = str(uuid4())

        now = datetime.now()
        if hasattr(model, "model_fields"):
            for fname, finfo in model.model_fields.items():
                if fname not in data and finfo.annotation in (datetime, date):
                    data[fname] = now.isoformat()

        record_id = str(data[pk_field])
        await self._client.set(
            self._key(model, record_id), self._serialize(data),
        )
        score = await self._client.incr(self._counter_key(model))
        await self._client.zadd(self._index_key(model), {record_id: score})

        return model.model_validate(data)

    async def update(
        self, model: type[T], id: Any, data: dict[str, Any],
    ) -> T | None:
        """Update a record in Redis. Sig: 2026-04-15 created"""
        key = self._key(model, str(id))
        raw = await self._client.get(key)
        if raw is None:
            return None
        record = self._deserialize(raw)
        record.update(data)
        await self._client.set(key, self._serialize(record))
        return model.model_validate(record)

    async def delete(self, model: type[T], id: Any) -> bool:
        """Delete a record from Redis. Sig: 2026-04-15 created"""
        record_id = str(id)
        key = self._key(model, record_id)
        existed = await self._client.delete(key)
        if existed:
            await self._client.zrem(self._index_key(model), record_id)
            return True
        return False

    async def count(
        self, model: type[T], *, filters: dict[str, Any] | None = None,
    ) -> int:
        """Count records, optionally filtered. Sig: 2026-04-15 created"""
        if not filters:
            return await self._client.zcard(self._index_key(model))
        items = await self.list(model, skip=0, limit=999999, filters=filters)
        return len(items)
