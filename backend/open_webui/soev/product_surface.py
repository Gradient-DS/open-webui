"""Mount retained upstream handlers without changing their source routers."""

from copy import copy

from fastapi import APIRouter


def product_router(router: APIRouter, *, exclude: set[str]) -> APIRouter:
    mounted = copy(router)
    mounted.routes = [route for route in router.routes if route.path not in exclude]
    return mounted


def product_permissions(permissions: dict) -> dict:
    retired = {
        'chat': {'continue_response', 'valves'},
        'features': {'direct_tool_servers', 'image_generation'},
        'sharing': {'tools', 'public_tools'},
        'workspace': {'tools', 'tools_import', 'tools_export'},
    }
    return {
        scope: {key: value for key, value in fields.items() if key not in retired.get(scope, set())}
        for scope, fields in permissions.items()
    }
