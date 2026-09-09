"""Read FastAPI registrations and admin dependencies without importing the app."""

import ast
from functools import lru_cache

from .seeds import METHODS, ROOT, SPEC


@lru_cache(maxsize=1)
def source_operations():
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
    result = {}
    for source, prefix, router, tree in mounts:
        functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        router_dependencies = []
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == router for t in node.targets)
                and isinstance(node.value, ast.Call)
            ):
                router_dependencies = [k.value for k in node.value.keywords if k.arg == 'dependencies']
                prefix += next((ast.literal_eval(k.value) for k in node.value.keywords if k.arg == 'prefix'), '')
        for function in functions.values():
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
                    else next(ast.literal_eval(k.value) for k in decorator.keywords if k.arg == 'methods')
                )
                roots = [
                    function.args,
                    *router_dependencies,
                    *(k.value for k in decorator.keywords if k.arg == 'dependencies'),
                ]
                gated = _admin_dependency(roots, functions, set())
                for method in methods:
                    result[f'{method.upper()} {path}'] = (source, function, gated)
    return result


def _admin_dependency(nodes, functions, seen):
    for root in nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.Name):
                if node.id == 'get_admin_user':
                    return True
                if node.id in functions and node.id not in seen:
                    if _admin_dependency([functions[node.id]], functions, seen | {node.id}):
                        return True
    return False


def admin_gated_operations(spec=SPEC):
    from .plane import operations

    inventory = source_operations()
    missing = set(operations(spec)) - inventory.keys()
    if missing:
        raise ValueError(f'Authorization inventory cannot resolve registered operations: {sorted(missing)}')
    return [route for route in operations(spec) if inventory[route][2]]
