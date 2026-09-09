"""Resolve the authorization inventory, including named HTTP-method constants."""

import ast
import re
from functools import lru_cache

from .authorization_surface import _admin_dependency
from .seeds import METHODS, ROOT, SPEC


def _mounts():
    root = ROOT / 'backend/open_webui'
    main = ast.parse((root / 'main.py').read_text())
    mounts = [(root / 'main.py', '', 'app', main)]
    for call in ast.walk(main):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == 'include_router'
            and call.args
        ):
            continue
        target = call.args[0]
        if not (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)):
            continue
        prefix = next((ast.literal_eval(k.value) for k in call.keywords if k.arg == 'prefix'), '')
        path = root / 'routers' / f'{target.value.id}.py'
        if path.exists():
            mounts.append((path, prefix, target.attr, ast.parse(path.read_text())))
    return mounts


def _registrations(function, router, prefix, constants):
    for decorator in function.decorator_list:
        if not (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id == router
        ):
            continue
        method = decorator.func.attr
        if method not in METHODS | {'api_route'} or not decorator.args:
            continue
        path = prefix + ast.literal_eval(decorator.args[0])
        methods = (
            [method]
            if method != 'api_route'
            else next(
                ast.literal_eval(constants[k.value.id] if isinstance(k.value, ast.Name) else k.value)
                for k in decorator.keywords
                if k.arg == 'methods'
            )
        )
        for method in methods:
            yield f'{method.upper()} {re.sub(r":[^}]+(?=})", "", path)}', decorator


def _direct_admin_guard(function):
    for node in function.body:
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        test = node.test
        if not (
            isinstance(test.left, ast.Attribute)
            and test.left.attr == 'role'
            and len(test.ops) == len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == 'admin'
        ):
            continue
        branch = (
            node.body if isinstance(test.ops[0], ast.NotEq) else node.orelse if isinstance(test.ops[0], ast.Eq) else []
        )
        if any(isinstance(statement, ast.Raise) for statement in branch):
            return True
    return False


@lru_cache(maxsize=1)
def source_operations():
    result = {}
    for source, prefix, router, tree in _mounts():
        constants = {
            target.id: node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        definitions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        functions = {n.name: n for n in definitions}
        router_dependencies = []
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == router for t in node.targets)
                and isinstance(node.value, ast.Call)
            ):
                router_dependencies = [k.value for k in node.value.keywords if k.arg == 'dependencies']
                prefix += next((ast.literal_eval(k.value) for k in node.value.keywords if k.arg == 'prefix'), '')
        for function in definitions:
            for route, decorator in _registrations(function, router, prefix, constants):
                roots = [
                    function.args,
                    *router_dependencies,
                    *(k.value for k in decorator.keywords if k.arg == 'dependencies'),
                ]
                gated = _admin_dependency(roots, functions, set()) or _direct_admin_guard(function)
                result[route] = (source, function, gated)
    return result


def admin_gated_operations(spec=SPEC):
    from .plane import operations

    inventory = source_operations()
    missing = set(operations(spec)) - inventory.keys()
    if missing:
        raise ValueError(f'Authorization inventory cannot resolve registered operations: {sorted(missing)}')
    return [route for route in operations(spec) if inventory[route][2]]
