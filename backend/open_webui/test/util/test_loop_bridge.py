"""Guards run_on_main_loop's failure mode.

Previously the helper silently fell back to ``asyncio.run(coro)`` when
``set_main_loop`` hadn't been called — which masked the actual bug
(Socket.IO emits dropping into a throwaway loop and poisoning the shared
Redis pool) until users complained about hung upload spinners. The new
behaviour: log an ERROR with a stable ``event`` tag, close the coroutine,
return ``None``. The frontend polling fallback in KnowledgeBase recovers
the user-visible spinner.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from open_webui.utils import loop_bridge


@pytest.fixture(autouse=True)
def _reset_main_loop():
    loop_bridge._MAIN_LOOP = None
    yield
    loop_bridge._MAIN_LOOP = None


def test_run_on_main_loop_drops_when_no_loop(caplog):
    async def noop():
        return 'should-not-run'

    coro = noop()
    with caplog.at_level(logging.ERROR, logger='open_webui.utils.loop_bridge'):
        result = loop_bridge.run_on_main_loop(coro)

    assert result is None
    # Coroutine must be closed — otherwise Python warns at GC time.
    with pytest.raises(RuntimeError):
        coro.send(None)
    assert any(getattr(rec, 'event', None) == 'loop_bridge.no_main_loop' for rec in caplog.records)


def test_run_on_main_loop_drops_when_loop_closed(caplog):
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    loop_bridge._MAIN_LOOP = closed_loop

    async def noop():
        return 'should-not-run'

    coro = noop()
    with caplog.at_level(logging.ERROR, logger='open_webui.utils.loop_bridge'):
        result = loop_bridge.run_on_main_loop(coro)

    assert result is None
    assert any(getattr(rec, 'event', None) == 'loop_bridge.no_main_loop' for rec in caplog.records)


def test_run_on_main_loop_dispatches_to_main_loop():
    """Happy path: a registered loop runs the coroutine and returns its result."""
    main_loop = asyncio.new_event_loop()

    import threading

    def _serve():
        main_loop.run_forever()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()

    try:
        loop_bridge.set_main_loop(main_loop)

        async def returns_value():
            return 42

        result = loop_bridge.run_on_main_loop(returns_value())
        assert result == 42
    finally:
        main_loop.call_soon_threadsafe(main_loop.stop)
        thread.join(timeout=2)
        main_loop.close()


def test_testing_env_var_preserves_run_fallback(monkeypatch):
    """OPEN_WEBUI_TESTING=1 keeps the old asyncio.run path so unit tests
    that exercise sync helpers without a uvicorn lifespan still work."""
    monkeypatch.setenv('OPEN_WEBUI_TESTING', '1')

    async def returns_value():
        return 7

    assert loop_bridge.run_on_main_loop(returns_value()) == 7
