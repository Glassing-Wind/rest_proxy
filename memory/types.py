"""Shared dataclasses and typed dicts for the memory layer."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Structured working memory (kept in Redis session state)
# ---------------------------------------------------------------------------


def _empty_working_memory() -> Dict[str, Any]:
    """Return a fresh, empty structured working memory dict."""
    return {
        "goal": "",
        "current_focus": "",
        "files_touched": [],
        "recent_errors": [],
        "decisions": [],
        "open_issues": [],
        "next_actions": [],
    }


# ---------------------------------------------------------------------------
# Dataclasses for durable Postgres records
# ---------------------------------------------------------------------------


@dataclass
class ConversationTurn:
    session_id: str
    turn_index: int
    role: str  # "user" | "assistant" | "tool"
    content: str  # full text (raw, may be large)
    compact_content: str  # compacted version for retrieval
    model: Optional[str] = None
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    id: Optional[str] = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class MemorySummary:
    session_id: str
    summary_text: str
    summary_type: str = "rolling"  # "rolling" | "checkpoint"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    id: Optional[str] = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class ToolOutput:
    session_id: str
    tool_name: str
    tool_call_id: str
    raw_output: str  # stored raw (may be truncated at db layer)
    compact_output: str  # compact summary, used in prompt assembly
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    id: Optional[str] = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class MemoryCheckpoint:
    session_id: str
    working_memory: Dict[str, Any]  # structured state JSON
    rolling_summary: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    id: Optional[str] = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class MemoryEmbedding:
    session_id: str
    ref_id: str  # id of the turn/summary being embedded
    ref_type: str  # "turn" | "summary"
    compact_text: str  # the text that was embedded
    vector: List[float]  # the embedding vector
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    id: Optional[str] = field(default_factory=lambda: str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Assembled memory augmentation (returned by retrieval assembler)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceReference:
    """Versioned pointer to evidence; unknown recovery/freshness stays explicit."""

    project_id: str
    session_id: str
    source_kind: str
    source_id: Optional[str] = None
    original_reference: Optional[str] = None
    content_hash: Optional[str] = None
    freshness: str = "unknown"
    original_availability: str = "unknown"
    schema_version: int = 1


@dataclass
class RetrievedEvidence:
    """Selected compact content with its source pointer and retrieval metadata."""

    compact_text: str
    reference: EvidenceReference
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskCheckpoint:
    """Scoped FIRE state contract; persistence and resume are separate operations."""

    project_id: str
    session_id: str
    task_id: str
    goal: str
    accepted_constraints: List[str] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    unresolved_questions: List[str] = field(default_factory=list)
    next_actions: List[str] = field(default_factory=list)
    evidence: List[EvidenceReference] = field(default_factory=list)
    schema_version: int = 1
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def matches_scope(self, project_id: str, session_id: str, task_id: str) -> bool:
        """Require all three scope components before restoring state."""
        return bool(project_id and session_id and task_id) and (
            self.project_id, self.session_id, self.task_id
        ) == (
            project_id, session_id, task_id
        )


@dataclass
class AssembledMemory:
    session_id: str
    rolling_summary: str
    working_memory: Dict[str, Any]
    recent_turns: List[Dict[str, Any]]  # list of {"role": ..., "content": ...}
    retrieved_snippets: List[str]  # compact text snippets from durable store
    assembled_text: str = ""  # final compact string for prompt injection
    retrieved_evidence: List[RetrievedEvidence] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.rolling_summary or self.recent_turns or self.retrieved_snippets
        )
