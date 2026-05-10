"""Pass 5: Lower IR to Python AST modules.

Sig: 2026-05-07 modified
"""
from __future__ import annotations

import ast

from restgen.ir.nodes import AppNode
from restgen.codegen.model_emitter import lower_models
from restgen.codegen.route_emitter import lower_routes, lower_router_group
from restgen.codegen.error_emitter import lower_errors
from restgen.codegen.middleware_emitter import lower_middleware
from restgen.codegen.app_emitter import lower_app, lower_init
from restgen.codegen.repo_emitter import lower_dependencies
from restgen.codegen.security_emitter import lower_security
from restgen.codegen.websocket_emitter import lower_websockets
from restgen.codegen.health_emitter import lower_health
from restgen.codegen.test_emitter import lower_tests


def lower(app: AppNode) -> dict[str, ast.Module]:
    """Lower IR into Python AST modules.

    Args:
        app: Optimized AppNode.

    Returns:
        Dict mapping filename to ast.Module:
        - "__init__.py" -> sys.path setup (only when handler refs exist)
        - "models.py" -> Pydantic model classes
        - "routes.py" -> FastAPI endpoint functions
        - "errors.py" -> Exception classes + handlers
        - "middleware.py" -> Middleware registration
        - "dependencies.py" -> Repository setup + DI
        - "security.py" -> Security scheme dependencies (only when schemes defined)
        - "app.py" -> Top-level FastAPI app wiring

    Sig: 2026-05-07 modified
    """
    modules: dict[str, ast.Module] = {}

    init_module = lower_init(app)
    if init_module is not None:
        modules["__init__.py"] = init_module

    modules["models.py"] = lower_models(app)

    if app.routers:
        # Mirrored layout: emit one module per router group.
        for group in app.routers:
            module_path = _router_module_path(group)
            modules[module_path] = lower_router_group(group, app)
    else:
        modules["routes.py"] = lower_routes(app)

    modules["errors.py"] = lower_errors(app)
    modules["middleware.py"] = lower_middleware(app)
    modules["dependencies.py"] = lower_dependencies(app)

    security_module = lower_security(app)
    if security_module is not None:
        modules["security.py"] = security_module

    ws_module = lower_websockets(app)
    if ws_module is not None:
        modules["websockets.py"] = ws_module

    health_module = lower_health(app)
    if health_module is not None:
        modules["health.py"] = health_module

    test_modules = lower_tests(app)
    if test_modules is not None:
        modules.update(test_modules)

    modules["app.py"] = lower_app(app)

    return modules


def _router_module_path(group) -> str:
    """Derive the output module path for a RouterGroupNode.

    Mirrors ``group.source_path`` into the ``routers/`` subtree when the
    router was imported from a sibling YAML; falls back to
    ``routers/<name>.py`` for inline-declared routers.

    Sig: 2026-04-24 created
    """
    if group.source_path is not None:
        rel = str(group.source_path)
        if rel.endswith((".yaml", ".yml")):
            rel = rel.rsplit(".", 1)[0] + ".py"
        else:
            rel = rel + ".py"
        if not rel.startswith("routers/"):
            # Preserve the imported file's directory structure but put it
            # under the routers/ subtree to keep the generated layout
            # self-contained.
            rel = "routers/" + rel
        return rel
    return f"routers/{group.name}.py"
