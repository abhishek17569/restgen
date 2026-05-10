from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ScalarType(Enum):
    """Supported scalar types in the DSL.

    Sig: 2026-05-07 modified
    """

    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    DATETIME = "datetime"
    DATE = "date"
    UUID = "uuid"
    BYTES = "bytes"
    ANY = "any"
    FILE = "file"


@dataclass(frozen=True)
class FieldType:
    """Resolved type for a model field.

    Exactly one of scalar, ref, list_of, dict_of should be set.
    optional wraps the type in Optional[...].

    Args:
        scalar: A scalar type from ScalarType enum.
        ref: Reference to another model by name.
        list_of: Inner FieldType for list[inner].
        dict_of: Tuple of (key_type, value_type) FieldTypes for dict[k, v].
        enum_values: Inline enum string values.
        optional: Whether this type is Optional.

    Sig: 2026-04-14 created
    """

    scalar: ScalarType | None = None
    ref: str | None = None
    list_of: FieldType | None = None
    dict_of: tuple[FieldType, FieldType] | None = None
    enum_values: list[str] | None = None
    optional: bool = False
