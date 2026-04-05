"""
Shared helpers for the rest_proxy MCP server.
"""


def get_memory_modules():
    """Lazy-load memory and proxy modules to speed up startup and prevent shutdown errors."""
    import memory.store as memory_store
    import memory.retrieval as memory_retrieval
    import memory.summary as memory_summary
    import memory.skeleton_extractor as skeleton_extractor
    import proxy.logging as proxy_logging
    return memory_store, memory_retrieval, memory_summary, skeleton_extractor, proxy_logging


def get_project_id(project_path: str) -> str:
    """Standardize project_id derivation (12-char MD5)."""
    import hashlib
    import os

    # Normalize: strip trailing slash, resolve absolute
    path = os.path.abspath(project_path).rstrip(os.sep)
    return hashlib.md5(path.encode()).hexdigest()[:12]


def normalize_neo4j_path(path: str) -> str:
    """Ensure path uses forward-slashes for Neo4j consistency."""
    if not path:
        return ""
    return path.replace("\\", "/")
