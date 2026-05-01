from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _ensure_sync_event_loop():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield
