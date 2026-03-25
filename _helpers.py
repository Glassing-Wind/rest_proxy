"""
Shared helpers for the rest_proxy MCP server.
"""


def get_memory_modules():
    """Lazy-load memory and proxy modules to speed up startup and prevent shutdown errors."""
    import memory_store
    import memory_retrieval
    import memory_summary
    import skeleton_extractor
    import proxy
    return memory_store, memory_retrieval, memory_summary, skeleton_extractor, proxy
