"""
Shared helpers for the rest_proxy MCP server.
Last updated: 2026-04-05
Building an Enterprise-Grade Indexing Pipeline with:
1. Atomic Synchronized Timestamps
2. Surgical Ghost-Chunk Pruning
3. Multi-phase status tracking
"""
import os


def _canonical_workspace_path(path: str | None) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    return os.path.realpath(os.path.abspath(raw)).rstrip(os.sep)


def _indexed_project_match(workspace_id: str) -> tuple[str, str] | None:
    candidate = _canonical_workspace_path(workspace_id)
    candidate_base = os.path.basename(str(workspace_id or "").rstrip(os.sep))
    if not candidate:
        candidate_base = os.path.basename(str(workspace_id or "").rstrip(os.sep))
    try:
        from graphrag_core.indexing import watcher

        indexed = watcher.load_indexed_projects()
    except Exception:
        return None
    basename_matches: list[tuple[str, str]] = []
    for project_id, entry in indexed.items():
        project_path = _canonical_workspace_path(entry.get("project_path"))
        if project_path and project_path == candidate:
            return project_id, entry.get("project_path") or workspace_id
        raw_path = str(entry.get("project_path") or "")
        if candidate_base and os.path.basename(raw_path.rstrip(os.sep)) == candidate_base:
            basename_matches.append((project_id, raw_path or workspace_id))
    if len(basename_matches) == 1:
        return basename_matches[0]
    return None


def get_memory_modules():
    """Lazy-load memory and proxy modules to speed up startup and prevent shutdown errors."""
    import memory.store as memory_store
    import memory.retrieval as memory_retrieval
    import memory.summary as memory_summary
    import memory.skeleton_extractor as skeleton_extractor
    import proxy.models as proxy_models
    return memory_store, memory_retrieval, memory_summary, skeleton_extractor, proxy_models


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
        if entry:
            return entry["project_id"]
        candidate = _canonical_workspace_path(workspace_id)
        if not candidate:
            return None
        for mapped in cls._mapping.values():
            mapped_path = _canonical_workspace_path(mapped.get("path"))
            if mapped_path and mapped_path == candidate:
                return mapped.get("project_id")
        indexed_match = _indexed_project_match(workspace_id)
        if indexed_match:
            return indexed_match[0]
        return None

    @classmethod
    def resolve_path(cls, workspace_id: str) -> str | None:
        """Get the local filesystem path from a logical ID."""
        cls._load()
        entry = cls._mapping.get(workspace_id)
        if entry:
            return entry["path"]
        candidate = _canonical_workspace_path(workspace_id)
        if not candidate:
            return None
        for mapped in cls._mapping.values():
            mapped_path = _canonical_workspace_path(mapped.get("path"))
            if mapped_path and mapped_path == candidate:
                return mapped.get("path")
        indexed_match = _indexed_project_match(workspace_id)
        if indexed_match:
            return indexed_match[1]
        return None


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

    # Normalize: strip trailing slash, resolve absolute
    path = _canonical_workspace_path(workspace_id)
    return hashlib.md5(path.encode()).hexdigest()[:12]


def get_workspace_path(workspace_id: str) -> str:
    """
    Resolve a workspace_id to a local filesystem path.
    Falls back to treating the ID as a path if not registered.
    """
    # Side-effect: trigger session re-binding if we're in an active session
    try:
        from tools.workspace_context import rebind_session_sync
        rebind_session_sync(workspace_id)
    except Exception:
        pass

    resolved = WorkspaceRegistry.resolve_path(workspace_id)
    if resolved:
        return resolved
    return workspace_id


def normalize_neo4j_path(path: str) -> str:
    """Ensure path uses forward-slashes for Neo4j consistency."""
    if not path:
        return ""
    return path.replace("\\", "/")
