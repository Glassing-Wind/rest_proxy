"""tools/docs/authored.py — index agent-authored documentation."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP

from tools.brain.docs.pipeline import index_authored_document


def _default_authored_url(topic: str, title: str) -> str:
    slug = quote(title.strip().replace(" ", "-").lower(), safe="-._/")
    return f"authored://{quote(topic, safe='-._/')}/{slug or 'untitled'}"


def _default_file_url(file_path: Path) -> str:
    return f"authored://local/{quote(file_path.resolve().as_posix().lstrip('/'), safe='-._/')}"


async def _index_authored_payload(
    *,
    topic: str,
    title: str,
    content: str,
    canonical_url: str,
    fmt: str,
    extra_metadata: dict,
) -> str:
    url = canonical_url.strip() or _default_authored_url(topic, title)
    result = await index_authored_document(
        topic=topic,
        url=url,
        title=title,
        content=content,
        fmt=fmt,
        replace_existing=True,
        extra_metadata=extra_metadata,
    )
    return (
        f"Indexed authored documentation.\n"
        f"  topic:  {result['topic']}\n"
        f"  title:  {result['title']}\n"
        f"  url:    {result['url']}\n"
        f"  chunks: {result['chunks']}\n"
        f"\nUse search_documentation(query, topic='{topic}') to retrieve it."
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def author_and_index_documentation(
        topic: str,
        title: str,
        content: str,
        canonical_url: str = "",
        fmt: str = "markdown",
    ) -> str:
        """
        Store a synthesized repo-specific guide in the docs index.

        Use this as the primary tool for the authored-guide workflow:
        1. inspect the target codebase
        2. browse official docs for the external system or API
        3. write a concise repo-specific guide
        4. save the final guide into the relevant repo's docs/ directory
           (create docs/ first if it does not exist)
        5. call this tool with the final guide content

        This is the preferred single-step indexing command for synthesized
        documentation. Use index_local_documentation_file() only when the guide
        already exists on disk and should be indexed from a file.

        Topic naming guidance:
        - Prefer repo-scoped topics for repo-specific work, such as
          '<repo>-<integration>' or '<repo>-<system>'.
        - Avoid generic topics unless cross-repo sharing is intentionally desired.

        Args:
            topic: Topic label under which to index the guide.
            title: Human-readable document title.
            content: Final markdown or XML content to index.
            canonical_url: Optional stable pseudo-URL for the guide. If omitted,
                           an authored:// URL is generated automatically.
            fmt: Chunking format ('markdown' or 'xml'). Default: markdown.
        """
        try:
            return await _index_authored_payload(
                topic=topic,
                title=title,
                content=content,
                canonical_url=canonical_url,
                fmt=fmt,
                extra_metadata={
                    "authored": True,
                    "fmt": fmt,
                    "workflow": "synthesized_guide",
                },
            )
        except Exception as e:
            return f"Error authoring/indexing documentation: {e}"

    @mcp.tool()
    async def index_authored_documentation(
        topic: str,
        title: str,
        content: str,
        canonical_url: str = "",
        fmt: str = "markdown",
    ) -> str:
        """
        Index agent-authored documentation so it is searchable via search_documentation().

        This is a compatibility alias for author_and_index_documentation().
        Prefer author_and_index_documentation() for the main synthesized-guide flow.
        In that workflow, the final guide should also be saved into the relevant
        repo's docs/ directory before indexing.

        Topic naming guidance:
        - Prefer repo-scoped topics for repo-specific work, such as
          '<repo>-<integration>' or '<repo>-<system>'.
        - Avoid generic topics unless cross-repo sharing is intentionally desired.

        Args:
            topic: Topic label under which to index the guide.
            title: Human-readable document title.
            content: Final markdown or XML content to index.
            canonical_url: Optional stable pseudo-URL for the guide. If omitted,
                           an authored:// URL is generated automatically.
            fmt: Chunking format ('markdown' or 'xml'). Default: markdown.
        """
        try:
            return await _index_authored_payload(
                topic=topic,
                title=title,
                content=content,
                canonical_url=canonical_url,
                fmt=fmt,
                extra_metadata={"authored": True, "fmt": fmt},
            )
        except Exception as e:
            return f"Error indexing authored documentation: {e}"

    @mcp.tool()
    async def index_local_documentation_file(
        topic: str,
        file_path: str,
        canonical_url: str = "",
        title: str = "",
    ) -> str:
        """
        Read a local documentation file and index it into doc_embeddings.

        Utility form of authored-doc indexing for guides that already exist on disk.
        Prefer author_and_index_documentation() when the final guide content is
        already available in memory.

        The intended authored-guide workflow is to save the final guide into the
        relevant repo's docs/ directory first, then index that saved file.

        Topic naming guidance:
        - Prefer repo-scoped topics for repo-specific work, such as
          '<repo>-<integration>' or '<repo>-<system>'.
        - Avoid generic topics unless cross-repo sharing is intentionally desired.

        Args:
            topic: Topic label under which to index the file.
            file_path: Absolute or repo-relative path to the documentation file.
            canonical_url: Optional stable pseudo-URL for indexing. If omitted,
                           an authored://local URL is generated from the file path.
            title: Optional display title. Defaults to the file name stem.
        """
        try:
            path = Path(file_path).expanduser()
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            if not path.is_file():
                return f"Error: file not found: {path}"

            content = path.read_text(encoding="utf-8")
            doc_title = title.strip() or path.stem.replace("_", " ").replace("-", " ").strip()
            fmt = "xml" if path.suffix.lower() == ".xml" else "markdown"
            url = canonical_url.strip() or _default_file_url(path)

            result = await index_authored_document(
                topic=topic,
                url=url,
                title=doc_title,
                content=content,
                fmt=fmt,
                replace_existing=True,
                extra_metadata={
                    "authored": True,
                    "fmt": fmt,
                    "file_path": str(path),
                },
            )
            return (
                f"Indexed local documentation file.\n"
                f"  topic:  {result['topic']}\n"
                f"  title:  {result['title']}\n"
                f"  file:   {path}\n"
                f"  url:    {result['url']}\n"
                f"  chunks: {result['chunks']}\n"
                f"\nUse search_documentation(query, topic='{topic}') to retrieve it."
            )
        except Exception as e:
            return f"Error indexing local documentation file: {e}"
