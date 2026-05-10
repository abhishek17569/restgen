"""Intermediate Representation for the restgen compiler.

Re-exports all public IR types and node classes.
"""

from restgen.ir.nodes import (
    MISSING,
    ActionKind,
    AppNode,
    AuthConfig,
    ComputedFieldNode,
    DatabaseConfig,
    ErrorNode,
    ErrorRef,
    FieldConstraints,
    FieldNode,
    FilterNode,
    HttpMethod,
    MiddlewareNode,
    ModelNode,
    PaginationConfig,
    PipelineStepNode,
    RouteNode,
)
from restgen.ir.types import FieldType, ScalarType

__all__ = [
    # types.py
    "ScalarType",
    "FieldType",
    # nodes.py
    "MISSING",
    "FieldConstraints",
    "FieldNode",
    "ComputedFieldNode",
    "ModelNode",
    "HttpMethod",
    "ActionKind",
    "PipelineStepNode",
    "ErrorRef",
    "ErrorNode",
    "FilterNode",
    "PaginationConfig",
    "RouteNode",
    "MiddlewareNode",
    "DatabaseConfig",
    "AuthConfig",
    "AppNode",
]
