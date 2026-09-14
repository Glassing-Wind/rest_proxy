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


@dataclass
class AssembledMemory:
    session_id: str
    rolling_summary: str
    working_memory: Dict[str, Any]
    recent_turns: List[Dict[str, Any]]  # list of {"role": ..., "content": ...}
    retrieved_snippets: List[str]  # compact text snippets from durable store
    assembled_text: str = ""  # final compact string for prompt injection

    def is_empty(self) -> bool:
        return not (
            self.rolling_summary or self.recent_turns or self.retrieved_snippets
        )
