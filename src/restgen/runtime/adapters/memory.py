"""In-memory repository adapter for testing and development.

Sig: 2026-04-14 created
"""
from __future__ import annotations
import copy
from typing import Any, TypeVar
from uuid import uuid4
from datetime import datetime, date

from ..repository import Repository

T = TypeVar("T")


class MemoryRepository(Repository):
    """In-memory repository using Python dicts.

    Stores records per model class in a dict keyed by primary key.
    Auto-generates UUIDs for primary keys if not provided.

    Sig: 2026-04-14 created
    """

    def __init__(self) -> None:
        # model_name -> {id -> record_dict}
        self._store: dict[str, dict[Any, dict[str, Any]]] = {}

    def _get_table(self, model: type) -> dict[Any, dict[str, Any]]:
        name = model.__name__
        if name not in self._store:
            self._store[name] = {}
        return self._store[name]

    def _get_pk_field(self, model: type) -> str:
        """Find the primary key field name from the model's fields.

        Convention: look for a field named 'id', or the first field.
        For Pydantic models, inspect model_fields.

        Sig: 2026-04-14 created
        """
        if hasattr(model, "model_fields"):
            fields = model.model_fields
            # Check for 'id' first
            if "id" in fields:
                return "id"
            # Return the first field as fallback
            return next(iter(fields))
        return "id"

    def _normalize_key(self, id: Any) -> str:
        """Normalize ID to string for consistent dict key lookup. Sig: 2026-04-15 created"""
        return str(id)

    async def get(self, model: type[T], id: Any) -> T | None:
        table = self._get_table(model)
        record = table.get(self._normalize_key(id))
        if record is None:
            return None
        return model.model_validate(copy.deepcopy(record))

    async def list(
        self,
        model: type[T],
        *,
        skip: int = 0,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
    ) -> list[T]:
        table = self._get_table(model)
        records = list(table.values())

        # Apply filters
        if filters:
            filtered = []
            for rec in records:
                match = all(rec.get(k) == v for k, v in filters.items() if v is not None)
                if match:
                    filtered.append(rec)
            records = filtered

        # Apply pagination
        records = records[skip : skip + limit]
        return [model.model_validate(copy.deepcopy(r)) for r in records]

    async def create(self, model: type[T], data: dict[str, Any]) -> T:
        table = self._get_table(model)
        pk_field = self._get_pk_field(model)

        # Auto-generate primary key if not provided
        if pk_field not in data or data[pk_field] is None:
            data[pk_field] = str(uuid4())

        # Auto-set datetime fields
        now = datetime.now()
        if hasattr(model, "model_fields"):
            for fname, finfo in model.model_fields.items():
                if fname not in data and finfo.annotation in (datetime, date):
                    data[fname] = now

        record = copy.deepcopy(data)
        table[record[pk_field]] = record
        return model.model_validate(copy.deepcopy(record))

    async def update(self, model: type[T], id: Any, data: dict[str, Any]) -> T | None:
        table = self._get_table(model)
        key = self._normalize_key(id)
        if key not in table:
            return None
        record = table[key]
        record.update(data)
        return model.model_validate(copy.deepcopy(record))

    async def delete(self, model: type[T], id: Any) -> bool:
        table = self._get_table(model)
        key = self._normalize_key(id)
        if key not in table:
            return False
        del table[key]
        return True

    async def count(self, model: type[T], *, filters: dict[str, Any] | None = None) -> int:
        table = self._get_table(model)
        if not filters:
            return len(table)
        count = 0
        for rec in table.values():
            if all(rec.get(k) == v for k, v in filters.items() if v is not None):
                count += 1
        return count
