"""Repository learning reads and explicit mutations shared by MCP and bounded REST."""
from __future__ import annotations

import os
from typing import Annotated, Any, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from memory.storage_config import embedded_graph_selected

MaxFiles = Annotated[int, Field(ge=1, le=64, strict=True)]
Revision = Annotated[int, Field(ge=0, strict=True)]
Limit = Annotated[int, Field(ge=1, le=50, strict=True)]
Offset = Annotated[int, Field(ge=0, le=1024, strict=True)]
RunID = Annotated[str, Field(min_length=1, max_length=128, strict=True)]
SourceID = Annotated[str, Field(min_length=1, max_length=256, strict=True)]
Text = Annotated[str, Field(min_length=1, max_length=16384, strict=True)]
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                            idempotentHint=True, openWorldHint=False)
LEARNING_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                 idempotentHint=False, openWorldHint=False)
LEARNING_DELETE = ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                  idempotentHint=False, openWorldHint=False)


async def get_repository_learning_profile(project_id: str, max_files: MaxFiles = 20) -> dict[str, Any]:
    """Read a RepoAnalyzer profile from at most 1..64 files in one embedded publication.

    Includes publication/source provenance and explicit coverage. This operation does
    not index, write learning state, load a model or inspect an arbitrary source root.
    Missing optional analyzer support returns an explicit unavailable/disabled status.
    """
    from graphrag_core.repository_learning.service import get_repository_learning_profile as profile

    return await profile(project_id, max_files=max_files)


async def compare_repository_learning_profiles(
    reference_project_id: str, target_project_id: str, max_files: MaxFiles = 20,
) -> dict[str, Any]:
    """Compare published reference/target RepoAnalyzer profiles, bounded to 1..64 files each.

    Returns cited structural comparisons and suggested transfers with their limitations.
    Suggestions are analysis, not verified repository facts or model training. No state
    is written and no model, index worker or second embedded owner is opened.
    """
    from graphrag_core.repository_learning.service import compare_repository_learning_profiles as compare

    return await compare(reference_project_id, target_project_id, max_files=max_files)


async def learn_repository_patterns(
    project_id: str, expected_run_id: RunID, expected_revision: Revision, max_files: MaxFiles = 20,
) -> dict[str, Any]:
    """Persist bounded code/architecture pattern artifacts from one explicit publication.

    Requires its exact run ID and learning-memory revision (zero for new memory).
    Writes only configured private learning memory; never reindexes or edits source.
    """
    from graphrag_core.repository_learning.patterns import learn_repository_patterns as learn

    return await learn(project_id, expected_run_id, expected_revision, max_files=max_files)


async def learn_text_patterns(
    project_id: str, source_id: SourceID, text: Text, expected_revision: Revision,
) -> dict[str, Any]:
    """Persist custom plaintext AST/pattern artifacts from explicitly supplied text.

    Text is limited to 16 KiB of UTF-8 bytes by the core service. Its source ID/hash
    remains caller-supplied text provenance, separate from published repository facts.
    Requires an exact learning-memory revision and never edits or indexes source files.
    """
    from graphrag_core.repository_learning.patterns import learn_text_patterns as learn

    return await learn(project_id, source_id, text, expected_revision)


async def learn_configuration_patterns(
    project_id: str, source_id: SourceID, text: Text, parser: Literal['requirements', 'gitignore'],
    expected_revision: Revision,
) -> dict[str, Any]:
    """Persist genuine GithubAnalyzer configuration observations from supplied text.

    Supports requirements/ignore declarations with exact source hash and component
    identity, not dependency solving or effective ignore evaluation. Requires a memory
    revision and optional administrator backend configuration. Never acquires a repo.
    """
    from graphrag_core.repository_learning.patterns import learn_configuration_patterns as learn

    return await learn(project_id, source_id, text, parser, expected_revision)


async def get_learned_repository_patterns(
    project_id: str, revision: Revision | None = None, limit: Limit = 20, offset: Offset = 0,
) -> dict[str, Any]:
    """Read bounded durable pattern artifacts/frequency with their revision and provenance.

    Use revision to select an exact historical memory snapshot; omissions and stale
    publication identity remain explicit. This inspection does not create learning state.
    """
    from graphrag_core.repository_learning.patterns import get_learned_repository_patterns as read

    return await read(project_id, revision=revision, limit=limit, offset=offset)


async def suggest_learned_repository_patterns(
    reference_project_id: str, target_project_id: str, reference_revision: Revision,
    max_files: MaxFiles = 20,
) -> dict[str, Any]:
    """Suggest pattern transfers from an exact learned revision to bounded target evidence.

    Suggestions disclose sampling and provenance and do not apply code changes, mutate
    learning memory or claim absence outside the captured target files.
    """
    from graphrag_core.repository_learning.patterns import suggest_learned_repository_patterns as suggest

    return await suggest(reference_project_id, target_project_id, reference_revision, max_files=max_files)


async def delete_repository_learning(project_id: str, expected_revision: Revision) -> dict[str, Any]:
    """Delete this project's private learning artifacts with an exact revision precondition.

    Explicit destructive action; it does not delete source, graph or retrieval publications.
    """
    from graphrag_core.repository_learning.patterns import delete_repository_learning as delete

    return await delete(project_id, expected_revision)


def register(mcp) -> None:
    """Expose reads for embedded state and mutations only with explicit learning state."""
    if embedded_graph_selected() and os.getenv('LM_PROXY_EMBEDDED_STATE', '').strip():
        mcp.tool(annotations=READ_ONLY)(get_repository_learning_profile)
        mcp.tool(annotations=READ_ONLY)(compare_repository_learning_profiles)
        mcp.tool(annotations=READ_ONLY)(get_learned_repository_patterns)
        mcp.tool(annotations=READ_ONLY)(suggest_learned_repository_patterns)
        if os.getenv('LM_PROXY_REPOSITORY_LEARNING_STATE', '').strip():
            mcp.tool(annotations=LEARNING_WRITE)(learn_repository_patterns)
            mcp.tool(annotations=LEARNING_WRITE)(learn_text_patterns)
            mcp.tool(annotations=LEARNING_WRITE)(learn_configuration_patterns)
            mcp.tool(annotations=LEARNING_DELETE)(delete_repository_learning)
