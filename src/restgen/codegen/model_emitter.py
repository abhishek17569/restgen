"""Emit Pydantic v2 model classes from IR ModelNodes.

Sig: 2026-04-14 created
"""
from __future__ import annotations

import ast
from collections import defaultdict

from restgen.ir.nodes import (
    AppNode,
    ComputedFieldNode,
    FieldNode,
    ModelNode,
    MISSING,
)
from restgen.ir.types import FieldType, ScalarType
from restgen.codegen.ast_builder import (
    collect_imports,
    make_annotation,
    make_class,
    make_constant,
    make_field_call,
    make_import_from,
    make_module,
    make_name,
    make_return,
    make_sync_func,
)


def _topo_sort_models(models: list[ModelNode]) -> list[ModelNode]:
    """Topologically sort models so base models appear before derived ones.

    Args:
        models: Unsorted list of ModelNode instances.

    Returns:
        Sorted list with base models first.

    Sig: 2026-04-14 created
    """
    by_name: dict[str, ModelNode] = {m.name: m for m in models}
    visited: set[str] = set()
    result: list[ModelNode] = []

    def visit(name: str) -> None:
        """Recursively visit model and its base. Sig: 2026-04-14 created"""
        if name in visited:
            return
        visited.add(name)
        model = by_name.get(name)
        if model is None:
            return
        if model.base and model.base in by_name:
            visit(model.base)
        for mixin in model.mixins:
            if mixin in by_name:
                visit(mixin)
        result.append(model)

    for m in models:
        visit(m.name)

    return result


def _emit_field(
    field_node: FieldNode,
    *,
    force_optional: bool = False,
) -> ast.AnnAssign:
    """Emit a single field as an ast.AnnAssign node.

    Args:
        field_node: The IR field node.
        force_optional: If True, make field optional with None default.

    Returns:
        An ast.AnnAssign statement.

    Sig: 2026-04-14 created
    """
    # Determine annotation
    ft = field_node.field_type

    # Email format override: use EmailStr instead of str
    if field_node.format == "email" and ft.scalar == ScalarType.STR:
        annotation: ast.expr = make_name("EmailStr")
        if ft.optional or force_optional:
            annotation = ast.BinOp(
                left=annotation,
                op=ast.BitOr(),
                right=make_constant(None),
            )
    else:
        if force_optional and not ft.optional:
            ft = FieldType(
                scalar=ft.scalar,
                ref=ft.ref,
                list_of=ft.list_of,
                dict_of=ft.dict_of,
                enum_values=ft.enum_values,
                optional=True,
            )
        annotation = make_annotation(ft)

    # Determine value (Field(...) call or default)
    value: ast.expr | None = None

    if force_optional and field_node.default is MISSING:
        # For all_optional derived models, default to None
        field_call = make_field_call(field_node)
        if field_call is not None:
            # Add default=None to the existing Field call
            assert isinstance(field_call, ast.Call)
            # Check if default already present
            has_default = any(
                kw.arg == "default" for kw in field_call.keywords
            )
            if not has_default:
                field_call.keywords.insert(
                    0,
                    ast.keyword(arg="default", value=make_constant(None)),
                )
            value = field_call
        else:
            value = make_constant(None)
    elif (field_node.optional or ft.optional) and field_node.default is MISSING:
        # Optional fields without explicit default get = None
        field_call = make_field_call(field_node)
        if field_call is not None:
            assert isinstance(field_call, ast.Call)
            has_default = any(kw.arg == "default" for kw in field_call.keywords)
            if not has_default:
                field_call.keywords.insert(0, ast.keyword(arg="default", value=make_constant(None)))
            value = field_call
        else:
            value = make_constant(None)
    else:
        value = make_field_call(field_node)

    return ast.AnnAssign(
        target=ast.Name(id=field_node.name, ctx=ast.Store()),
        annotation=annotation,
        value=value,
        simple=1,
    )


def _emit_computed_field(cf: ComputedFieldNode) -> list[ast.stmt]:
    """Emit a computed field as a @computed_field @property method.

    Args:
        cf: The IR computed field node.

    Returns:
        List containing the decorated property method.

    Sig: 2026-04-14 created
    """
    # Parse handler into module and function name
    parts = cf.handler.rsplit(".", 1)
    if len(parts) == 2:
        handler_module, handler_func = parts
    else:
        handler_module = "utils"
        handler_func = cf.handler

    # Build lazy import + return body
    lazy_import = ast.ImportFrom(
        module=handler_module,
        names=[ast.alias(name=handler_func)],
        level=0,
    )
    return_stmt = make_return(
        ast.Call(
            func=make_name(handler_func),
            args=[make_name("self")],
            keywords=[],
        ),
    )

    return_annotation = make_annotation(cf.field_type)

    func = make_sync_func(
        name=cf.name,
        args=[("self", None, None)],
        body=[lazy_import, return_stmt],
        returns=return_annotation,
        decorators=[make_name("computed_field"), make_name("property")],
    )

    return [func]


def _collect_all_imports(
    models: list[ModelNode],
) -> dict[str, set[str]]:
    """Collect all import requirements across all models.

    Args:
        models: List of all ModelNode instances.

    Returns:
        Dict mapping module path to set of names to import.

    Sig: 2026-04-14 created
    """
    imports: dict[str, set[str]] = defaultdict(set)

    # Always needed
    imports["pydantic"].update({"BaseModel", "Field", "ConfigDict"})

    has_computed = False
    has_email = False
    has_literal = False

    for model in models:
        fields = model.resolved_fields if model.resolved_fields else model.fields

        for f in fields:
            # Collect type imports
            for mod, name in collect_imports(f.field_type):
                imports[mod].add(name)
                if name == "Literal":
                    has_literal = True

            # Email format
            if f.format == "email":
                has_email = True

        for cf in model.computed_fields:
            has_computed = True
            for mod, name in collect_imports(cf.field_type):
                imports[mod].add(name)

    if has_computed:
        imports["pydantic"].add("computed_field")
    if has_email:
        imports["pydantic"].add("EmailStr")
    if has_literal:
        imports["typing"].add("Literal")

    return imports


def _has_primary_field(fields: list[FieldNode]) -> bool:
    """Check if any field is marked as primary.

    Args:
        fields: List of field nodes to inspect.

    Returns:
        True if any field has primary=True.

    Sig: 2026-04-14 created
    """
    return any(f.primary for f in fields)


def lower_models(app: AppNode) -> ast.Module:
    """Generate models.py AST from all ModelNodes.

    Uses resolved_fields if available, otherwise falls back to fields.

    For each model:
    1. Create ClassDef inheriting from BaseModel (+ mixins if any)
    2. For each field, create ast.AnnAssign with type annotation
    3. If field has constraints/default, assign Field(...) call
    4. If field.format == "email", use EmailStr instead of str
    5. For computed fields, generate @computed_field + @property method
    6. If model has is_derived and all_optional, all fields are X | None = None

    Auto-add model_config = ConfigDict(from_attributes=True) if any field is primary.

    Args:
        app: The root IR AppNode containing all models.

    Returns:
        An ast.Module representing the complete models.py file.

    Sig: 2026-04-14 created
    """
    sorted_models = _topo_sort_models(app.models)

    # Collect and emit imports
    all_imports = _collect_all_imports(sorted_models)
    import_stmts: list[ast.stmt] = []

    # Sort modules for deterministic output
    for mod in sorted(all_imports.keys()):
        names = sorted(all_imports[mod])
        import_stmts.append(make_import_from(mod, names))

    # Emit class definitions
    class_defs: list[ast.stmt] = []

    for model in sorted_models:
        fields = model.resolved_fields if model.resolved_fields else model.fields
        force_optional = model.is_derived and model.all_optional

        # Determine bases — DSL `base` is for field derivation only, not Python inheritance.
        # Only use mixins (from optimize pass) as actual Python bases.
        bases: list[str] = []
        if model.mixins:
            bases.extend(model.mixins)
        bases.append("BaseModel")

        # Build class body
        body: list[ast.stmt] = []

        # Add model_config if any primary field
        if _has_primary_field(fields):
            config_call = ast.Call(
                func=make_name("ConfigDict"),
                args=[],
                keywords=[
                    ast.keyword(
                        arg="from_attributes",
                        value=make_constant(True),
                    ),
                ],
            )
            body.append(
                ast.Assign(
                    targets=[ast.Name(id="model_config", ctx=ast.Store())],
                    value=config_call,
                    lineno=0,
                ),
            )

        # Emit fields
        for f in fields:
            body.append(_emit_field(f, force_optional=force_optional))

        # Emit computed fields
        for cf in model.computed_fields:
            body.extend(_emit_computed_field(cf))

        # Empty body guard
        if not body:
            body.append(ast.Pass())

        class_defs.append(make_class(model.name, bases, body))

    return make_module(import_stmts + class_defs)
