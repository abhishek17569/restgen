"""AST construction helpers for restgen codegen.

Sig: 2026-04-14 created
"""
from __future__ import annotations

import ast
from typing import Any

from restgen.ir.types import FieldType, ScalarType
from restgen.ir.nodes import FieldNode, FieldConstraints, MISSING


# Scalar -> Python type name mapping
_SCALAR_TO_PYTYPE: dict[ScalarType, str] = {
    ScalarType.STR: "str",
    ScalarType.INT: "int",
    ScalarType.FLOAT: "float",
    ScalarType.BOOL: "bool",
    ScalarType.DATETIME: "datetime",
    ScalarType.DATE: "date",
    ScalarType.UUID: "UUID",
    ScalarType.BYTES: "bytes",
    ScalarType.ANY: "Any",
}

# Scalar -> import module mapping
_SCALAR_IMPORTS: dict[ScalarType, tuple[str, str]] = {
    ScalarType.DATETIME: ("datetime", "datetime"),
    ScalarType.DATE: ("datetime", "date"),
    ScalarType.UUID: ("uuid", "UUID"),
    ScalarType.ANY: ("typing", "Any"),
}


def make_name(id: str) -> ast.Name:
    """Create an ast.Name node with Load context.

    Args:
        id: The identifier string.

    Returns:
        An ast.Name node.

    Sig: 2026-04-14 created
    """
    return ast.Name(id=id, ctx=ast.Load())


def make_constant(value: Any) -> ast.Constant:
    """Create an ast.Constant node.

    Args:
        value: The literal value.

    Returns:
        An ast.Constant node.

    Sig: 2026-04-14 created
    """
    return ast.Constant(value=value)


def make_import_from(module: str, names: list[str]) -> ast.ImportFrom:
    """Build a ``from module import name1, name2, ...`` statement.

    Args:
        module: Dotted module path.
        names: Names to import.

    Returns:
        An ast.ImportFrom node.

    Sig: 2026-04-14 created
    """
    return ast.ImportFrom(
        module=module,
        names=[ast.alias(name=n) for n in names],
        level=0,
    )


def make_annotation(field_type: FieldType) -> ast.expr:
    """Convert an IR FieldType to a Python type annotation AST node.

    Handles scalar types, refs, list_of, dict_of, enum_values, and optional wrapping.

    Args:
        field_type: The IR field type to convert.

    Returns:
        An ast.expr representing the Python type annotation.

    Sig: 2026-04-14 created
    """
    inner: ast.expr

    if field_type.scalar is not None:
        inner = make_name(_SCALAR_TO_PYTYPE[field_type.scalar])
    elif field_type.ref is not None:
        inner = make_name(field_type.ref)
    elif field_type.list_of is not None:
        inner = ast.Subscript(
            value=make_name("list"),
            slice=make_annotation(
                FieldType(
                    scalar=field_type.list_of.scalar,
                    ref=field_type.list_of.ref,
                    list_of=field_type.list_of.list_of,
                    dict_of=field_type.list_of.dict_of,
                    enum_values=field_type.list_of.enum_values,
                    optional=field_type.list_of.optional,
                ),
            ),
            ctx=ast.Load(),
        )
    elif field_type.dict_of is not None:
        key_type, val_type = field_type.dict_of
        inner = ast.Subscript(
            value=make_name("dict"),
            slice=ast.Tuple(
                elts=[make_annotation(key_type), make_annotation(val_type)],
                ctx=ast.Load(),
            ),
            ctx=ast.Load(),
        )
    elif field_type.enum_values is not None:
        inner = ast.Subscript(
            value=make_name("Literal"),
            slice=ast.Tuple(
                elts=[make_constant(v) for v in field_type.enum_values],
                ctx=ast.Load(),
            ),
            ctx=ast.Load(),
        )
    else:
        inner = make_name("Any")

    if field_type.optional:
        return ast.BinOp(
            left=inner,
            op=ast.BitOr(),
            right=make_constant(None),
        )

    return inner


def make_field_call(field_node: FieldNode) -> ast.expr | None:
    """Build a Pydantic ``Field(...)`` call from field constraints and defaults.

    Returns None if no constraints, format, or default are present.

    Args:
        field_node: The IR field node.

    Returns:
        An ast.Call for ``Field(...)`` or None.

    Sig: 2026-04-14 created
    """
    keywords: list[ast.keyword] = []

    # Default value
    if field_node.default is not MISSING:
        keywords.append(ast.keyword(arg="default", value=make_constant(field_node.default)))

    # Description
    if field_node.description:
        keywords.append(ast.keyword(arg="description", value=make_constant(field_node.description)))

    # Constraints
    if field_node.constraints is not None:
        c = field_node.constraints
        for attr in ("min_length", "max_length", "ge", "le", "gt", "lt", "multiple_of"):
            val = getattr(c, attr, None)
            if val is not None:
                keywords.append(ast.keyword(arg=attr, value=make_constant(val)))
        if c.regex is not None:
            keywords.append(ast.keyword(arg="pattern", value=make_constant(c.regex)))

    if not keywords:
        return None

    return ast.Call(
        func=make_name("Field"),
        args=[],
        keywords=keywords,
    )


def make_async_func(
    name: str,
    args: list[tuple[str, ast.expr | None, ast.expr | None]] | ast.arguments,
    body: list[ast.stmt],
    returns: ast.expr | None = None,
    decorators: list[ast.expr] | None = None,
) -> ast.AsyncFunctionDef:
    """Build an async function definition.

    Args:
        name: Function name.
        args: List of (name, annotation, default) tuples, or a pre-built ast.arguments.
        body: Function body statements.
        returns: Return type annotation.
        decorators: Decorator expressions.

    Returns:
        An ast.AsyncFunctionDef node.

    Sig: 2026-04-14 modified
    """
    arguments = args if isinstance(args, ast.arguments) else _build_arguments(args)
    return ast.AsyncFunctionDef(
        name=name,
        args=arguments,
        body=body or [ast.Pass()],
        decorator_list=decorators or [],
        returns=returns,
        type_params=[],
    )


def make_sync_func(
    name: str,
    args: list[tuple[str, ast.expr | None, ast.expr | None]] | ast.arguments,
    body: list[ast.stmt],
    returns: ast.expr | None = None,
    decorators: list[ast.expr] | None = None,
) -> ast.FunctionDef:
    """Build a synchronous function definition.

    Args:
        name: Function name.
        args: List of (name, annotation, default) tuples, or a pre-built ast.arguments.
        body: Function body statements.
        returns: Return type annotation.
        decorators: Decorator expressions.

    Returns:
        An ast.FunctionDef node.

    Sig: 2026-04-14 modified
    """
    arguments = args if isinstance(args, ast.arguments) else _build_arguments(args)
    return ast.FunctionDef(
        name=name,
        args=arguments,
        body=body or [ast.Pass()],
        decorator_list=decorators or [],
        returns=returns,
        type_params=[],
    )


def _build_arguments(
    args: list[tuple[str, ast.expr | None, ast.expr | None]],
) -> ast.arguments:
    """Build ast.arguments from a list of (name, annotation, default) tuples.

    Args:
        args: List of (name, annotation, default) tuples.

    Returns:
        An ast.arguments node.

    Sig: 2026-04-14 created
    """
    arg_nodes: list[ast.arg] = []
    defaults: list[ast.expr] = []

    for arg_name, annotation, default in args:
        arg_nodes.append(ast.arg(arg=arg_name, annotation=annotation))
        if default is not None:
            defaults.append(default)

    return ast.arguments(
        posonlyargs=[],
        args=arg_nodes,
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=defaults,
    )


def make_class(
    name: str,
    bases: list[str],
    body: list[ast.stmt],
    decorators: list[ast.expr] | None = None,
) -> ast.ClassDef:
    """Build a class definition.

    Args:
        name: Class name.
        bases: Base class names.
        body: Class body statements.
        decorators: Decorator expressions.

    Returns:
        An ast.ClassDef node.

    Sig: 2026-04-14 created
    """
    return ast.ClassDef(
        name=name,
        bases=[make_name(b) for b in bases],
        keywords=[],
        body=body or [ast.Pass()],
        decorator_list=decorators or [],
        type_params=[],
    )


def make_assign(target: str, value: ast.expr) -> ast.Assign:
    """Build a simple assignment statement ``target = value``.

    Args:
        target: Variable name.
        value: Right-hand side expression.

    Returns:
        An ast.Assign node.

    Sig: 2026-04-14 created
    """
    return ast.Assign(
        targets=[ast.Name(id=target, ctx=ast.Store())],
        value=value,
        lineno=0,
    )


def make_await(expr: ast.expr) -> ast.Await:
    """Wrap an expression in an Await node.

    Args:
        expr: The awaitable expression.

    Returns:
        An ast.Await node.

    Sig: 2026-04-14 created
    """
    return ast.Await(value=expr)


def make_return(value: ast.expr) -> ast.Return:
    """Build a return statement.

    Args:
        value: The return value expression.

    Returns:
        An ast.Return node.

    Sig: 2026-04-14 created
    """
    return ast.Return(value=value)


def make_raise_http_exception(status: int, detail: ast.expr) -> ast.Raise:
    """Build ``raise HTTPException(status_code=N, detail=...)``.

    Args:
        status: HTTP status code.
        detail: Detail expression for the error message.

    Returns:
        An ast.Raise node.

    Sig: 2026-04-14 created
    """
    return ast.Raise(
        exc=ast.Call(
            func=make_name("HTTPException"),
            args=[],
            keywords=[
                ast.keyword(arg="status_code", value=make_constant(status)),
                ast.keyword(arg="detail", value=detail),
            ],
        ),
        cause=None,
    )


def make_if_none_raise_404(
    var_name: str, detail: str = "Not found",
) -> ast.If:
    """Build ``if var_name is None: raise HTTPException(status_code=404, ...)``.

    Args:
        var_name: Variable to check for None.
        detail: Error detail message.

    Returns:
        An ast.If node.

    Sig: 2026-04-14 created
    """
    return ast.If(
        test=ast.Compare(
            left=make_name(var_name),
            ops=[ast.Is()],
            comparators=[make_constant(None)],
        ),
        body=[make_raise_http_exception(404, make_constant(detail))],
        orelse=[],
    )


def make_if_none_raise_named(
    var_name: str, error_class: str,
) -> ast.If:
    """Build ``if var_name is None: raise ErrorClass()``.

    Args:
        var_name: Variable to check for None.
        error_class: Name of the exception class to raise.

    Returns:
        An ast.If node.

    Sig: 2026-04-15 created
    """
    return ast.If(
        test=ast.Compare(
            left=make_name(var_name),
            ops=[ast.Is()],
            comparators=[make_constant(None)],
        ),
        body=[
            ast.Raise(
                exc=ast.Call(
                    func=make_name(error_class),
                    args=[],
                    keywords=[],
                ),
                cause=None,
            )
        ],
        orelse=[],
    )


def collect_imports(field_type: FieldType) -> list[tuple[str, str]]:
    """Collect (module, name) import pairs needed for a FieldType.

    Args:
        field_type: The IR field type to inspect.

    Returns:
        List of (module, name) tuples for required imports.

    Sig: 2026-04-14 created
    """
    imports: list[tuple[str, str]] = []

    if field_type.scalar is not None and field_type.scalar in _SCALAR_IMPORTS:
        imports.append(_SCALAR_IMPORTS[field_type.scalar])
    elif field_type.list_of is not None:
        imports.extend(collect_imports(field_type.list_of))
    elif field_type.dict_of is not None:
        imports.extend(collect_imports(field_type.dict_of[0]))
        imports.extend(collect_imports(field_type.dict_of[1]))
    elif field_type.enum_values is not None:
        imports.append(("typing", "Literal"))

    return imports


def make_module(body: list[ast.stmt]) -> ast.Module:
    """Wrap statements in an ast.Module.

    Args:
        body: Top-level statements.

    Returns:
        An ast.Module node with fix_missing_locations applied.

    Sig: 2026-04-14 created
    """
    mod = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(mod)
    return mod
