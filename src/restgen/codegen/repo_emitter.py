"""Emit the dependency injection module (dependencies.py) and repository setup.

Sig: 2026-04-14 created
"""
from __future__ import annotations

import ast
import re

from restgen.ir.nodes import AppNode, DatabaseConfig
from restgen.codegen.ast_builder import (
    make_name,
    make_constant,
    make_import_from,
    make_module,
    make_assign,
    make_async_func,
    make_return,
    make_await,
)


# ---------------------------------------------------------------------------
# Adapter mapping
# ---------------------------------------------------------------------------

_DB_TYPE_MAP: dict[str, tuple[str, str]] = {
    "postgres": ("PostgresRepository", ".runtime.adapters.postgres"),
    "mongo": ("MongoRepository", ".runtime.adapters.mongo"),
    "sqlite": ("SqliteRepository", ".runtime.adapters.sqlite"),
    "redis": ("RedisRepository", ".runtime.adapters.redis"),
    "memory": ("MemoryRepository", ".runtime.adapters.memory"),
}

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _url_expr(url: str) -> ast.expr:
    """Build an AST expression for a connection URL string.

    If the URL contains ``${VAR_NAME}`` placeholders, generates
    ``os.environ["VAR_NAME"]`` lookups joined with string fragments
    via an f-string. Otherwise returns a plain constant.

    Args:
        url: The raw connection URL string.

    Returns:
        An ast.expr representing the URL value.

    Sig: 2026-04-14 created
    """
    env_vars = _ENV_VAR_RE.findall(url)
    if not env_vars:
        return make_constant(url)

    # If the entire URL is a single env var reference, emit os.environ["VAR"]
    if _ENV_VAR_RE.fullmatch(url):
        return ast.Subscript(
            value=ast.Attribute(
                value=make_name("os"),
                attr="environ",
                ctx=ast.Load(),
            ),
            slice=make_constant(env_vars[0]),
            ctx=ast.Load(),
        )

    # For mixed strings, build an f-string using JoinedStr
    values: list[ast.expr] = []
    last_end = 0
    for match in _ENV_VAR_RE.finditer(url):
        # Literal text before the match
        if match.start() > last_end:
            values.append(make_constant(url[last_end:match.start()]))
        # os.environ["VAR"] as a FormattedValue
        var_lookup = ast.Subscript(
            value=ast.Attribute(
                value=make_name("os"),
                attr="environ",
                ctx=ast.Load(),
            ),
            slice=make_constant(match.group(1)),
            ctx=ast.Load(),
        )
        values.append(
            ast.FormattedValue(
                value=var_lookup,
                conversion=-1,
                format_spec=None,
            ),
        )
        last_end = match.end()
    # Trailing literal
    if last_end < len(url):
        values.append(make_constant(url[last_end:]))

    return ast.JoinedStr(values=values)


def _needs_os_import(db: DatabaseConfig | None) -> bool:
    """Check whether the database URL uses environment variable placeholders.

    Args:
        db: The database configuration, or None.

    Returns:
        True if os import is needed.

    Sig: 2026-04-14 created
    """
    if db is None or db.url is None:
        return False
    return bool(_ENV_VAR_RE.search(db.url))


def lower_dependencies(app: AppNode) -> ast.Module:
    """Generate dependencies.py AST with get_repository and lifespan.

    Based on database config type, imports the right adapter:
    - "postgres" -> from .runtime.adapters.postgres import PostgresRepository
    - "mongo" -> from .runtime.adapters.mongo import MongoRepository
    - "sqlite" -> from .runtime.adapters.sqlite import SqliteRepository
    - "redis" -> from .runtime.adapters.redis import RedisRepository
    - "memory" or None -> from .runtime.adapters.memory import MemoryRepository

    Generated code:
        from contextlib import asynccontextmanager
        from .runtime.adapters.memory import MemoryRepository

        _repo = MemoryRepository(url="...")  # or no url for memory

        @asynccontextmanager
        async def lifespan(app):
            await _repo.connect()
            yield
            await _repo.disconnect()

        async def get_repository():
            return _repo

    Args:
        app: The root IR AppNode containing database configuration.

    Returns:
        An ast.Module representing the complete dependencies.py file.

    Sig: 2026-04-14 created
    """
    body: list[ast.stmt] = []
    db = app.database

    # Determine adapter class and module
    db_type = (db.db_type if db and db.db_type else "memory").lower()
    class_name, adapter_module = _DB_TYPE_MAP.get(
        db_type,
        _DB_TYPE_MAP["memory"],
    )

    # -- Imports ---------------------------------------------------------------
    if _needs_os_import(db):
        body.append(make_import_from("os", ["environ"]))
        # We still use os.environ below, so also import os
        body.append(
            ast.Import(names=[ast.alias(name="os")]),
        )

    body.append(make_import_from("contextlib", ["asynccontextmanager"]))

    # Adapter import: try relative first, fall back to absolute for standalone mode
    relative_adapter = [make_import_from(adapter_module, [class_name])]
    absolute_adapter = [make_import_from(adapter_module.lstrip("."), [class_name])]
    body.append(ast.Try(
        body=relative_adapter,
        handlers=[ast.ExceptHandler(type=make_name("ImportError"), name=None, body=absolute_adapter)],
        orelse=[], finalbody=[],
    ))

    # -- _repo = AdapterClass(url=...) -----------------------------------------
    constructor_keywords: list[ast.keyword] = []
    if db and db.url:
        constructor_keywords.append(
            ast.keyword(arg="url", value=_url_expr(db.url)),
        )

    repo_assign = make_assign(
        "_repo",
        ast.Call(
            func=make_name(class_name),
            args=[],
            keywords=constructor_keywords,
        ),
    )
    body.append(repo_assign)

    # -- @asynccontextmanager / async def lifespan(app): -----------------------
    # Build: await _repo.connect()
    connect_stmt = ast.Expr(
        value=make_await(
            ast.Call(
                func=ast.Attribute(
                    value=make_name("_repo"),
                    attr="connect",
                    ctx=ast.Load(),
                ),
                args=[],
                keywords=[],
            ),
        ),
    )
    # Build: yield
    yield_stmt = ast.Expr(value=ast.Yield(value=None))
    # Build: await _repo.disconnect()
    disconnect_stmt = ast.Expr(
        value=make_await(
            ast.Call(
                func=ast.Attribute(
                    value=make_name("_repo"),
                    attr="disconnect",
                    ctx=ast.Load(),
                ),
                args=[],
                keywords=[],
            ),
        ),
    )

    lifespan_func = make_async_func(
        name="lifespan",
        args=[("app", None, None)],
        body=[connect_stmt, yield_stmt, disconnect_stmt],
        decorators=[make_name("asynccontextmanager")],
    )
    body.append(lifespan_func)

    # -- async def get_repository(): -------------------------------------------
    get_repo_func = make_async_func(
        name="get_repository",
        args=[],
        body=[make_return(make_name("_repo"))],
    )
    body.append(get_repo_func)

    return make_module(body)
