"""Compatibility wrapper for memory_store imports."""

from memory import store_core as _store_core
from memory.store_core import *  # noqa: F401,F403


def __getattr__(name: str):
    if name in {"search_docs", "doc_type_from_result"}:
        from memory import docs_retrieval as dr
        return getattr(dr, name)
    if name == "search_codebase_core":
        from memory import code_retrieval as cr
        return getattr(cr, name)
    if name == "trace_symbol_cross_project_core":
        from memory import cross_project_trace as cpt
        return getattr(cpt, name)
    return getattr(_store_core, name)
