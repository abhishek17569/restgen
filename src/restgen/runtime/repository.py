"""Abstract repository interface for generated APIs.

Sig: 2026-04-14 created
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, TypeVar

T = TypeVar("T")


class Repository(ABC):
    """Database-agnostic repository interface.

    Generated route code depends on this interface.
    Concrete adapters (postgres, mongo, memory, etc.) implement it.

    Sig: 2026-04-14 created
    """

    @abstractmethod
    async def get(self, model: type[T], id: Any) -> T | None:
        """Fetch a single record by primary key.

        Args:
            model: The Pydantic model class.
            id: Primary key value.

        Returns:
            Model instance or None if not found.

        Sig: 2026-04-14 created
        """
        ...

    @abstractmethod
    async def list(
        self,
        model: type[T],
        *,
        skip: int = 0,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
    ) -> list[T]:
        """List records with pagination and optional filters.

        Args:
            model: The Pydantic model class.
            skip: Number of records to skip.
            limit: Maximum number of records to return.
            filters: Key-value filter pairs.

        Returns:
            List of model instances.

        Sig: 2026-04-14 created
        """
        ...

    @abstractmethod
    async def create(self, model: type[T], data: dict[str, Any]) -> T:
        """Create a new record.

        Args:
            model: The Pydantic model class.
            data: Record data as a dict.

        Returns:
            Created model instance.

        Sig: 2026-04-14 created
        """
        ...

    @abstractmethod
    async def update(self, model: type[T], id: Any, data: dict[str, Any]) -> T | None:
        """Update a record by primary key.

        Args:
            model: The Pydantic model class.
            id: Primary key value.
            data: Fields to update.

        Returns:
            Updated model instance, or None if not found.

        Sig: 2026-04-14 created
        """
        ...

    @abstractmethod
    async def delete(self, model: type[T], id: Any) -> bool:
        """Delete a record by primary key.

        Args:
            model: The Pydantic model class.
            id: Primary key value.

        Returns:
            True if deleted, False if not found.

        Sig: 2026-04-14 created
        """
        ...

    @abstractmethod
    async def count(self, model: type[T], *, filters: dict[str, Any] | None = None) -> int:
        """Count records matching optional filters.

        Sig: 2026-04-14 created
        """
        ...

    async def connect(self) -> None:
        """Establish connection. Default no-op. Sig: 2026-04-14 created"""

    async def disconnect(self) -> None:
        """Close connection. Default no-op. Sig: 2026-04-14 created"""
