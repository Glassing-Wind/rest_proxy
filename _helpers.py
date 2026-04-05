"""
Shared helpers for the rest_proxy MCP server.
Last updated: 2026-04-05
Building an Enterprise-Grade Indexing Pipeline with:
1. Atomic Synchronized Timestamps
2. Surgical Ghost-Chunk Pruning
3. Multi-phase status tracking
"""
import os


def get_memory_modules():
    """Lazy-load memory and proxy modules to speed up startup and prevent shutdown errors."""
    import memory.store as memory_store
    import memory.retrieval as memory_retrieval
    import memory.summary as memory_summary
    import memory.skeleton_extractor as skeleton_extractor
    import proxy.logging as proxy_logging
    return memory_store, memory_retrieval, memory_summary, skeleton_extractor, proxy_logging


class WorkspaceRegistry:
    """
    Mapping layer for Decentralized Brain/Hands.
    Resolves a logical `workspace_id` to its Neo4j `project_id` and local path.
    """

    # In-memory mapping (for Phase 1).
    # Structure: { workspace_id: {"project_id": "...", "path": "..."} }
    _mapping: dict[str, dict[str, str]] = {}
    _initialized: bool = False

    @classmethod
    def _get_storage_path(cls) -> str:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        runtime_dir = os.path.join(base_dir, ".runtime")
        os.makedirs(runtime_dir, exist_ok=True)
        return os.path.join(runtime_dir, "workspaces.json")

    @classmethod
    def _load(cls):
        if cls._initialized:
            return
        import json
        path = cls._get_storage_path()
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    cls._mapping = json.load(f)
            except Exception:
                pass
        cls._initialized = True

    @classmethod
    def _persist(cls):
        import json
        path = cls._get_storage_path()
        try:
            with open(path, "w") as f:
                json.dump(cls._mapping, f, indent=2)
        except Exception:
            pass

    @classmethod
    def register(cls, workspace_id: str, project_id: str, path: str | None = None):
        """Map a logical ID to a physical project hash and optional local path."""
        cls._load()
        cls._mapping[workspace_id] = {
            "project_id": project_id,
            "path": path or workspace_id,
        }
        cls._persist()

    @classmethod
    def resolve_id(cls, workspace_id: str) -> str | None:
        """Get the physical project hash from a logical ID."""
        cls._load()
        entry = cls._mapping.get(workspace_id)
        return entry["project_id"] if entry else None

    @classmethod
    def resolve_path(cls, workspace_id: str) -> str | None:
        """Get the local filesystem path from a logical ID."""
        cls._load()
        entry = cls._mapping.get(workspace_id)
        return entry["path"] if entry else None


def get_project_id(workspace_id: str) -> str:
    """
    Standardize project_id derivation (12-char MD5).
    Falls back to normal hashing if workspace_id is not a known registered ID.
    """
    # 1. First check if it's a registered workspace_id.
    #    (Enables decentralized 'Brain' reasoning).
    resolved = WorkspaceRegistry.resolve_id(workspace_id)
    if resolved:
        return resolved

    # 2. Traditional path-based hashing.
    #    (For local 'Hands' agents and backward compatibility).
    import hashlib
    import os

    # Normalize: strip trailing slash, resolve absolute
    path = os.path.abspath(workspace_id).rstrip(os.sep)
    return hashlib.md5(path.encode()).hexdigest()[:12]


def get_workspace_path(workspace_id: str) -> str:
    """
    Resolve a workspace_id to a local filesystem path.
    Falls back to treating the ID as a path if not registered.
    """
    resolved = WorkspaceRegistry.resolve_path(workspace_id)
    if resolved:
        return resolved
    return workspace_id


def normalize_neo4j_path(path: str) -> str:
    """Ensure path uses forward-slashes for Neo4j consistency."""
    if not path:
        return ""
    return path.replace("\\", "/")
