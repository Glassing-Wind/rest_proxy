"""Verify retained source identity and bounded citations without live services."""
import hashlib
import json
from pathlib import Path


def verify(root: Path) -> dict:
    root = root.resolve()
    receipt = json.loads((root / "finding.json").read_text())
    if receipt.get("schema_version") != 1:
        raise ValueError("Unsupported receipt schema")
    sources = {}
    for source in receipt["sources"]:
        target = (root / source["snapshot"]).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Snapshot escapes receipt directory")
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise ValueError(f"Source hash mismatch: {source['snapshot']}")
        lines = len(data.splitlines())
        if lines != source["line_count"]:
            raise ValueError("Source line count mismatch")
        if source["snapshot"] in sources:
            raise ValueError("Duplicate snapshot identity")
        sources[source["snapshot"]] = lines
    for finding in receipt["findings"]:
        if not finding["citations"]:
            raise ValueError("Finding lacks retained citation")
        for citation in finding["citations"]:
            lines = sources[citation["snapshot"]]
            if not 1 <= citation["start_line"] <= citation["end_line"] <= lines:
                raise ValueError("Citation outside retained source")
    return {"source_snapshots": len(sources), "findings": len(receipt["findings"]),
            "source_binding": "passed", "semantic_correctness": "requires review"}


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).parent), indent=2))
