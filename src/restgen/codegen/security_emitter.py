"""Emit security dependency module from IR SecuritySchemeNodes.

Generates a ``security.py`` module containing FastAPI security scheme objects
and async dependency functions. The generated code has ZERO runtime imports
from restgen; it relies only on ``fastapi`` and user-provided verify handlers.

Sig: 2026-05-07 created
"""
from __future__ import annotations

import ast

from restgen.ir.nodes import AppNode, SecuritySchemeNode, SecuritySchemeType
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
# FastAPI class mapping
# ---------------------------------------------------------------------------

# OAuth2 flow -> (fastapi.security class name, tokenUrl keyword)
_OAUTH2_FLOW_CLASS: dict[str, str] = {
    "password": "OAuth2PasswordBearer",
    "client_credentials": "OAuth2PasswordBearer",
}

# API key location -> fastapi.security class name
_APIKEY_LOCATION_CLASS: dict[str, str] = {
    "header": "APIKeyHeader",
    "query": "APIKeyQuery",
    "cookie": "APIKeyCookie",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_handler(dotted: str) -> tuple[str, str]:
    """Split a dotted handler path into (module, attribute).

    Args:
        dotted: A dotted path like ``auth_module.verify_token``.

    Returns:
        Tuple of (module_path, function_name).

    Sig: 2026-05-07 created
    """
    parts = dotted.rsplit(".", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", dotted


def _scheme_var_name(scheme: SecuritySchemeNode) -> str:
    """Return the module-level variable name for a scheme object.

    Args:
        scheme: The security scheme node.

    Returns:
        Variable name such as ``oauth2_scheme`` or ``api_key_header``.

    Sig: 2026-05-07 created
    """
    if scheme.scheme_type is SecuritySchemeType.OAUTH2:
        return f"{scheme.name}_scheme"
    if scheme.scheme_type is SecuritySchemeType.APIKEY:
        return f"{scheme.name}_{scheme.location}"
    return f"{scheme.name}_scheme"


def _dep_func_name(scheme: SecuritySchemeNode) -> str:
    """Return the dependency function name for a scheme.

    Args:
        scheme: The security scheme node.

    Returns:
        Function name like ``get_current_user_<scheme_name>``.

    Sig: 2026-05-07 created
    """
    return f"get_current_user_{scheme.name}"


def _scheme_class(scheme: SecuritySchemeNode) -> str:
    """Resolve the fastapi.security class name for a scheme.

    Args:
        scheme: The security scheme node.

    Returns:
        The class name to import from ``fastapi.security``.

    Raises:
        ValueError: If the scheme type or location is unsupported.

    Sig: 2026-05-07 created
    """
    if scheme.scheme_type is SecuritySchemeType.OAUTH2:
        cls = _OAUTH2_FLOW_CLASS.get(scheme.flow)
        if cls is None:
            raise ValueError(f"Unsupported OAuth2 flow: {scheme.flow}")
        return cls
    if scheme.scheme_type is SecuritySchemeType.APIKEY:
        cls = _APIKEY_LOCATION_CLASS.get(scheme.location)
        if cls is None:
            raise ValueError(f"Unsupported APIKey location: {scheme.location}")
        return cls
    if scheme.scheme_type is SecuritySchemeType.BASIC:
        return "HTTPBasic"
    raise ValueError(f"Unsupported security scheme type: {scheme.scheme_type}")


def _param_name(scheme: SecuritySchemeNode) -> str:
    """Return the dependency function's parameter name for a scheme.

    Args:
        scheme: The security scheme node.

    Returns:
        Parameter name passed to the verify handler.

    Sig: 2026-05-07 created
    """
    if scheme.scheme_type is SecuritySchemeType.OAUTH2:
        return "token"
    if scheme.scheme_type is SecuritySchemeType.APIKEY:
        return "api_key"
    if scheme.scheme_type is SecuritySchemeType.BASIC:
        return "credentials"
    return "value"


def _param_annotation(scheme: SecuritySchemeNode) -> ast.expr:
    """Return the parameter type annotation AST for the dependency function.

    Args:
        scheme: The security scheme node.

    Returns:
        An ast.expr for the parameter annotation.

    Sig: 2026-05-07 created
    """
    if scheme.scheme_type is SecuritySchemeType.BASIC:
        return make_name("HTTPBasicCredentials")
    return make_name("str")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _build_scheme_assign(scheme: SecuritySchemeNode) -> ast.Assign:
    """Build the module-level assignment for a security scheme object.

    Generates statements like:
        oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
        api_key_header = APIKeyHeader(name="X-API-Key")
        basic_scheme = HTTPBasic()

    Args:
        scheme: The security scheme node.

    Returns:
        An ast.Assign node.

    Sig: 2026-05-07 created
    """
    cls_name = _scheme_class(scheme)
    keywords: list[ast.keyword] = []

    if scheme.scheme_type is SecuritySchemeType.OAUTH2:
        keywords.append(
            ast.keyword(arg="tokenUrl", value=make_constant(scheme.token_url)),
        )
        if scheme.scopes:
            scope_keys = [make_constant(k) for k in scheme.scopes]
            scope_vals = [make_constant(v) for v in scheme.scopes.values()]
            keywords.append(
                ast.keyword(
                    arg="scopes",
                    value=ast.Dict(keys=scope_keys, values=scope_vals),
                ),
            )
    elif scheme.scheme_type is SecuritySchemeType.APIKEY:
        keywords.append(
            ast.keyword(arg="name", value=make_constant(scheme.header_name)),
        )

    call = ast.Call(
        func=make_name(cls_name),
        args=[],
        keywords=keywords,
    )
    return make_assign(_scheme_var_name(scheme), call)


def _build_dep_func(scheme: SecuritySchemeNode) -> ast.AsyncFunctionDef:
    """Build the async dependency function for a security scheme.

    Generates:
        async def get_current_user_<name>(
            <param>: <Anno> = Depends(<scheme_var>),
        ):
            return await <verify_fn>(<param>=<param>)

    Args:
        scheme: The security scheme node.

    Returns:
        An ast.AsyncFunctionDef node.

    Sig: 2026-05-07 created
    """
    param_name = _param_name(scheme)
    scheme_var = _scheme_var_name(scheme)
    _, verify_fn = _split_handler(scheme.verify_handler)

    depends_call = ast.Call(
        func=make_name("Depends"),
        args=[make_name(scheme_var)],
        keywords=[],
    )

    args = [
        (param_name, _param_annotation(scheme), depends_call),
    ]

    verify_call = ast.Call(
        func=make_name(verify_fn),
        args=[],
        keywords=[
            ast.keyword(arg=param_name, value=make_name(param_name)),
        ],
    )

    body: list[ast.stmt] = [
        make_return(make_await(verify_call)),
    ]

    return make_async_func(
        name=_dep_func_name(scheme),
        args=args,
        body=body,
    )


def _collect_fastapi_security_classes(
    schemes: list[SecuritySchemeNode],
) -> list[str]:
    """Collect the set of ``fastapi.security`` class names needed.

    HTTPBasic additionally pulls in HTTPBasicCredentials for annotations.

    Args:
        schemes: The list of security scheme nodes.

    Returns:
        A sorted list of class names to import from ``fastapi.security``.

    Sig: 2026-05-07 created
    """
    needed: set[str] = set()
    for scheme in schemes:
        needed.add(_scheme_class(scheme))
        if scheme.scheme_type is SecuritySchemeType.BASIC:
            needed.add("HTTPBasicCredentials")
    return sorted(needed)


def _collect_handler_imports(
    schemes: list[SecuritySchemeNode],
) -> list[tuple[str, str]]:
    """Collect (module, function) pairs for user verify handlers.

    Duplicates are removed; handlers with no module prefix are skipped.

    Args:
        schemes: The list of security scheme nodes.

    Returns:
        A list of (module, function) tuples in declaration order.

    Sig: 2026-05-07 created
    """
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []
    for scheme in schemes:
        if not scheme.verify_handler:
            continue
        module, fn = _split_handler(scheme.verify_handler)
        if not module:
            continue
        pair = (module, fn)
        if pair in seen:
            continue
        seen.add(pair)
        ordered.append(pair)
    return ordered


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lower_security(app: AppNode) -> ast.Module | None:
    """Generate ``security.py`` AST from the app's security schemes.

    For each scheme, emits:
      1. A module-level FastAPI scheme object (e.g. ``OAuth2PasswordBearer``).
      2. An async dependency function named ``get_current_user_<scheme_name>``
         that resolves the credential via ``Depends`` and awaits the user's
         ``verify_handler``.

    Returns ``None`` when ``app.security_schemes`` is empty so the caller can
    skip writing the file.

    Args:
        app: The root IR application node.

    Returns:
        An ``ast.Module`` representing ``security.py`` or ``None`` if no
        security schemes are configured.

    Sig: 2026-05-07 created
    """
    schemes = app.security_schemes
    if not schemes:
        return None

    body: list[ast.stmt] = []

    # -- fastapi imports -------------------------------------------------------
    body.append(make_import_from("fastapi", ["Depends"]))

    security_classes = _collect_fastapi_security_classes(schemes)
    if security_classes:
        body.append(make_import_from("fastapi.security", security_classes))

    # -- user verify_handler imports ------------------------------------------
    for module, fn in _collect_handler_imports(schemes):
        body.append(make_import_from(module, [fn]))

    # -- per-scheme objects and dependency functions --------------------------
    for scheme in schemes:
        body.append(_build_scheme_assign(scheme))
        body.append(_build_dep_func(scheme))

    return make_module(body)
