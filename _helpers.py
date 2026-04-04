"""
Shared helpers for the rest_proxy MCP server.
"""


def get_memory_modules():
    """Lazy-load memory and proxy modules to speed up startup and prevent shutdown errors."""
    import memory.store as memory_store
    import memory.retrieval as memory_retrieval
    import memory.summary as memory_summary
    import memory.skeleton_extractor as skeleton_extractor
    import proxy.app as proxy

    return memory_store, memory_retrieval, memory_summary, skeleton_extractor, proxy
