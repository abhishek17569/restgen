"""Emit error exception classes and handler registration from IR ErrorNodes.

Sig: 2026-04-14 created
"""
from __future__ import annotations

import ast
import re

from restgen.ir.nodes import AppNode, ErrorNode
from restgen.codegen.ast_builder import (
    make_name,
    make_constant,
    make_import_from,
    make_async_func,
    make_class,
    make_module,
    make_return,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_pascal_case(name: str) -> str:
    """Convert a snake_case or PascalCase name to PascalCase.

    Args:
        name: The input name string.

    Returns:
        PascalCase version of the name.

    Sig: 2026-04-14 created
    """
    # If already PascalCase (no underscores, starts with upper), keep it
    if "_" not in name and name[0:1].isupper():
        return name
    # Convert snake_case to PascalCase
    return "".join(word.capitalize() for word in name.split("_"))


def _error_class_name(error_name: str) -> str:
    """Derive exception class name from error name, ensuring 'Error' suffix.

    Args:
        error_name: The raw error name from the IR.

    Returns:
        PascalCase class name ending in 'Error'.

    Sig: 2026-04-14 created
    """
    pascal = _to_pascal_case(error_name)
    if not pascal.endswith("Error"):
        pascal += "Error"
    return pascal


def _build_error_class(error: ErrorNode) -> ast.ClassDef:
    """Build an exception class AST node for an ErrorNode.

    Args:
        error: The error definition from the IR.

    Returns:
        An ast.ClassDef node for the exception class.

    Sig: 2026-04-14 created
    """
    class_name = _error_class_name(error.name)
    default_detail = error.body.get("message", error.name) if error.body else error.name

    # Build __init__ method
    init_args = ast.arguments(
        posonlyargs=[],
        args=[
            ast.arg(arg="self"),
            ast.arg(arg="detail", annotation=make_name("str")),
        ],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=ast.arg(arg="kwargs"),
        defaults=[make_constant(default_detail)],
    )

    # super().__init__(status_code=N, detail=detail, **kwargs)
    super_call = ast.Call(
        func=ast.Attribute(
            value=ast.Call(func=make_name("super"), args=[], keywords=[]),
            attr="__init__",
            ctx=ast.Load(),
        ),
        args=[],
        keywords=[
            ast.keyword(arg="status_code", value=make_constant(error.status)),
            ast.keyword(arg="detail", value=make_name("detail")),
            ast.keyword(arg=None, value=make_name("kwargs")),
        ],
    )

    init_func = ast.FunctionDef(
        name="__init__",
        args=init_args,
        body=[ast.Expr(value=super_call)],
        decorator_list=[],
        returns=None,
        lineno=0,
        col_offset=0,
        end_lineno=None,
        end_col_offset=None,
    )

    return make_class(
        name=class_name,
        bases=["HTTPException"],
        body=[init_func],
    )


def _build_handler_func(error: ErrorNode) -> ast.AsyncFunctionDef:
    """Build an exception handler async function for an ErrorNode.

    Args:
        error: The error definition from the IR.

    Returns:
        An ast.AsyncFunctionDef for the exception handler.

    Sig: 2026-04-14 created
    """
    class_name = _error_class_name(error.name)

    handler_args = ast.arguments(
        posonlyargs=[],
        args=[
            ast.arg(arg="request", annotation=make_name("Request")),
            ast.arg(arg="exc", annotation=make_name(class_name)),
        ],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )

    # return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    json_response = ast.Call(
        func=make_name("JSONResponse"),
        args=[],
        keywords=[
            ast.keyword(
                arg="status_code",
                value=ast.Attribute(value=make_name("exc"), attr="status_code", ctx=ast.Load()),
            ),
            ast.keyword(
                arg="content",
                value=ast.Dict(
                    keys=[make_constant("detail")],
                    values=[ast.Attribute(value=make_name("exc"), attr="detail", ctx=ast.Load())],
                ),
            ),
        ],
    )

    handler_name = f"handle_{_to_snake_case(class_name)}"

    return make_async_func(
        name=handler_name,
        args=handler_args,
        body=[make_return(json_response)],
        decorators=[],
    )


def _to_snake_case(name: str) -> str:
    """Convert PascalCase to snake_case.

    Args:
        name: PascalCase string.

    Returns:
        snake_case string.

    Sig: 2026-04-14 created
    """
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


# ---------------------------------------------------------------------------
# Generic exception handlers
# ---------------------------------------------------------------------------


def _build_generic_http_exception_handler() -> ast.AsyncFunctionDef:
    """Build a generic HTTPException handler for consistent JSON responses.

    Generated code:
        async def handle_http_exception(request: Request, exc: HTTPException):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    Sig: 2026-05-08 created
    """
    handler_args = ast.arguments(
        posonlyargs=[],
        args=[
            ast.arg(arg="request", annotation=make_name("Request")),
            ast.arg(arg="exc", annotation=make_name("HTTPException")),
        ],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )

    json_response = ast.Call(
        func=make_name("JSONResponse"),
        args=[],
        keywords=[
            ast.keyword(
                arg="status_code",
                value=ast.Attribute(value=make_name("exc"), attr="status_code", ctx=ast.Load()),
            ),
            ast.keyword(
                arg="content",
                value=ast.Dict(
                    keys=[make_constant("detail")],
                    values=[ast.Attribute(value=make_name("exc"), attr="detail", ctx=ast.Load())],
                ),
            ),
        ],
    )

    return make_async_func(
        name="handle_http_exception",
        args=handler_args,
        body=[make_return(json_response)],
        decorators=[],
    )


def _build_catchall_500_handler() -> ast.AsyncFunctionDef:
    """Build a catch-all handler for unhandled exceptions (500).

    Generated code:
        async def handle_unexpected_error(request: Request, exc: Exception):
            return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    Sig: 2026-05-08 created
    """
    handler_args = ast.arguments(
        posonlyargs=[],
        args=[
            ast.arg(arg="request", annotation=make_name("Request")),
            ast.arg(arg="exc", annotation=make_name("Exception")),
        ],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )

    json_response = ast.Call(
        func=make_name("JSONResponse"),
        args=[],
        keywords=[
            ast.keyword(arg="status_code", value=make_constant(500)),
            ast.keyword(
                arg="content",
                value=ast.Dict(
                    keys=[make_constant("detail")],
                    values=[make_constant("Internal server error")],
                ),
            ),
        ],
    )

    return make_async_func(
        name="handle_unexpected_error",
        args=handler_args,
        body=[make_return(json_response)],
        decorators=[],
    )


def _build_validation_error_handler() -> ast.AsyncFunctionDef:
    """Build a RequestValidationError handler (structured 422).

    Generated code:
        async def handle_validation_error(request: Request, exc: RequestValidationError):
            return JSONResponse(
                status_code=422,
                content={"detail": "Validation error", "errors": exc.errors()},
            )

    Sig: 2026-05-08 created
    """
    handler_args = ast.arguments(
        posonlyargs=[],
        args=[
            ast.arg(arg="request", annotation=make_name("Request")),
            ast.arg(arg="exc", annotation=make_name("RequestValidationError")),
        ],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )

    errors_call = ast.Call(
        func=ast.Attribute(value=make_name("exc"), attr="errors", ctx=ast.Load()),
        args=[],
        keywords=[],
    )

    json_response = ast.Call(
        func=make_name("JSONResponse"),
        args=[],
        keywords=[
            ast.keyword(arg="status_code", value=make_constant(422)),
            ast.keyword(
                arg="content",
                value=ast.Dict(
                    keys=[make_constant("detail"), make_constant("errors")],
                    values=[make_constant("Validation error"), errors_call],
                ),
            ),
        ],
    )

    return make_async_func(
        name="handle_validation_error",
        args=handler_args,
        body=[make_return(json_response)],
        decorators=[],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lower_errors(app: AppNode) -> ast.Module:
    """Generate errors.py AST with exception classes and registration function.

    For each ErrorNode, generate:
    - A class inheriting from HTTPException with default status and detail.
    - An entry in register_error_handlers().

    Args:
        app: The root IR application node containing error definitions.

    Returns:
        An ast.Module representing the complete errors.py file.

    Sig: 2026-04-14 created
    """
    body: list[ast.stmt] = []

    # Imports
    body.append(make_import_from("fastapi", ["HTTPException", "Request", "FastAPI"]))
    body.append(make_import_from("fastapi.exceptions", ["RequestValidationError"]))
    body.append(make_import_from("fastapi.responses", ["JSONResponse"]))

    # Error classes
    for error in app.errors:
        body.append(_build_error_class(error))

    # register_error_handlers function
    reg_args = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg="app", annotation=make_name("FastAPI"))],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )

    reg_body: list[ast.stmt] = []
    for error in app.errors:
        class_name = _error_class_name(error.name)
        handler_func = _build_handler_func(error)

        # @app.exception_handler(ClassName)
        decorator = ast.Call(
            func=ast.Attribute(value=make_name("app"), attr="exception_handler", ctx=ast.Load()),
            args=[make_name(class_name)],
            keywords=[],
        )
        handler_func.decorator_list = [decorator]
        reg_body.append(handler_func)

    # Generic HTTPException handler (consistent JSON format)
    http_exc_handler = _build_generic_http_exception_handler()
    http_exc_decorator = ast.Call(
        func=ast.Attribute(value=make_name("app"), attr="exception_handler", ctx=ast.Load()),
        args=[make_name("HTTPException")],
        keywords=[],
    )
    http_exc_handler.decorator_list = [http_exc_decorator]
    reg_body.append(http_exc_handler)

    # Catch-all 500 handler for unhandled exceptions
    catchall_handler = _build_catchall_500_handler()
    catchall_decorator = ast.Call(
        func=ast.Attribute(value=make_name("app"), attr="exception_handler", ctx=ast.Load()),
        args=[make_name("Exception")],
        keywords=[],
    )
    catchall_handler.decorator_list = [catchall_decorator]
    reg_body.append(catchall_handler)

    # RequestValidationError handler (structured 422)
    validation_handler = _build_validation_error_handler()
    validation_decorator = ast.Call(
        func=ast.Attribute(value=make_name("app"), attr="exception_handler", ctx=ast.Load()),
        args=[make_name("RequestValidationError")],
        keywords=[],
    )
    validation_handler.decorator_list = [validation_decorator]
    reg_body.append(validation_handler)

    reg_func = ast.FunctionDef(
        name="register_error_handlers",
        args=reg_args,
        body=reg_body,
        decorator_list=[],
        returns=make_constant(None),
        lineno=0,
        col_offset=0,
        end_lineno=None,
        end_col_offset=None,
    )
    body.append(reg_func)

    return make_module(body)
