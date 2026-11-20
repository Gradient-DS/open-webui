"""Mount retained upstream handlers without changing their source routers."""

from copy import copy

from fastapi import APIRouter


def product_router(router: APIRouter, *, exclude: set[str]) -> APIRouter:
    mounted = copy(router)
    mounted.routes = [route for route in router.routes if route.path not in exclude]
    return mounted
