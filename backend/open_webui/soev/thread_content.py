"""Fail closed at legacy content boundaries while the replacement path is dark."""

from functools import wraps
from inspect import iscoroutinefunction

from open_webui.soev import threads


def refuse_local_content() -> None:
    if threads.enabled():
        raise threads.ThreadError(409, 'unavailable')


def legacy_content(cls):
    """Guard every table operation, including indirect callers outside routers."""
    for name, method in vars(cls).copy().items():
        if name.startswith('__'):
            continue
        if isinstance(method, (staticmethod, classmethod)):
            setattr(cls, name, type(method)(_guard(method.__func__)))
        elif callable(method):
            setattr(cls, name, _guard(method))
    return cls


def _guard(method):
    if iscoroutinefunction(method):

        @wraps(method)
        async def guarded(*args, **kwargs):
            refuse_local_content()
            return await method(*args, **kwargs)
    else:

        @wraps(method)
        def guarded(*args, **kwargs):
            refuse_local_content()
            return method(*args, **kwargs)

    return guarded


def legacy_event_emitter(factory):
    @wraps(factory)
    async def guarded(*args, **kwargs):
        if threads.enabled() and kwargs.get('update_db') is not False:
            return _discard_event
        return await factory(*args, **kwargs)

    return guarded


async def _discard_event(event_data) -> None:
    pass
