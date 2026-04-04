"""Compatibility wrapper for memory_store imports."""

from memory import store_core as _store_core
from memory.store_core import *  # noqa: F401,F403


def __getattr__(name: str):
    return getattr(_store_core, name)
