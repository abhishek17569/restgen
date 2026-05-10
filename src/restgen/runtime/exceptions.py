"""Base exception classes for generated APIs.

Sig: 2026-04-14 created
"""
from __future__ import annotations


class RestgenError(Exception):
    """Base exception for restgen runtime errors. Sig: 2026-04-14 created"""


class EntityNotFoundError(RestgenError):
    """Raised when a requested entity does not exist.

    Args:
        model_name: Name of the model.
        entity_id: ID of the entity.

    Sig: 2026-04-14 created
    """
    def __init__(self, model_name: str, entity_id: str | int):
        self.model_name = model_name
        self.entity_id = entity_id
        super().__init__(f"{model_name} with id {entity_id!r} not found")


class DuplicateEntityError(RestgenError):
    """Raised when creating an entity that already exists.

    Sig: 2026-04-14 created
    """
    def __init__(self, model_name: str, field: str, value: str):
        self.model_name = model_name
        self.field = field
        self.value = value
        super().__init__(f"{model_name} with {field}={value!r} already exists")
