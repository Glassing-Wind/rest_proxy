"""Report formatting helpers for duplication detection."""

from __future__ import annotations

from tools.brain.search import duplication_helpers as dup_helpers


def append_group_pairs(
    lines: list[str],
    *,
    title: str,
    groups: list[list[dict]],
    cross_file: bool,
    max_pairs: int,
    same_file_allowed,
    same_file_counts: dict[str, int] | None = None,
) -> None:
    if not groups:
        lines.append(f"{title}: none")
        return
    lines.append(f"{title} ({len(groups)})")
    count = 0
    for group in groups:
        pair = dup_helpers.pick_pair(group, cross_file=cross_file)
        if not pair:
            continue
        a, b = pair
        if not cross_file and same_file_counts is not None:
            file_key = a["file_path"]
            if not same_file_allowed(file_key, a.get("content") or "", same_file_counts):
                continue
        meta_a = a.get("metadata") or {}
        meta_b = b.get("metadata") or {}
        a_start = meta_a.get("start_line")
        b_start = meta_b.get("start_line")
        a_line = f":{a_start}" if isinstance(a_start, int) else ""
        b_line = f":{b_start}" if isinstance(b_start, int) else ""
        preview_a = dup_helpers.preview_line(a.get("content") or "")
        preview_b = dup_helpers.preview_line(b.get("content") or "")
        lines.append(f"- {a['file_path']}{a_line} ↔ {b['file_path']}{b_line}")
        lines.append(f"  A: {preview_a}")
        lines.append(f"  B: {preview_b}")
        if not cross_file and same_file_counts is not None:
            same_file_counts[file_key] = same_file_counts.get(file_key, 0) + 1
        count += 1
        if count >= max_pairs:
            break


def append_semantic_pairs(
    lines: list[str],
    *,
    results: list[dict],
    max_pairs: int,
) -> None:
    if results:
        lines.append(
            "\nNear-duplicate chunks (cross-file only, "
            "min_similarity shown below)"
        )
        for row in results[:max_pairs]:
            sim = row["sim"]
            meta_a = row.get("meta_a") or {}
            meta_b = row.get("meta_b") or {}
            a_start = meta_a.get("start_line")
            b_start = meta_b.get("start_line")
            a_line = f":{a_start}" if isinstance(a_start, int) else ""
            b_line = f":{b_start}" if isinstance(b_start, int) else ""
            preview_a = dup_helpers.preview_line(row.get("content_a") or "")
            preview_b = dup_helpers.preview_line(row.get("content_b") or "")
            lines.append(
                f"- {row['file_a']}{a_line} ↔ {row['file_b']}{b_line}  (sim={sim:.3f})"
            )
            lines.append(f"  A: {preview_a}")
            lines.append(f"  B: {preview_b}")
    else:
        lines.append("\nNo near-duplicate chunks found.")


def append_winnow_pairs(
    lines: list[str],
    *,
    title: str,
    pairs: list[tuple[dict, dict, float, float]],
    max_pairs: int,
    same_file_allowed,
    same_file_counts: dict[str, int] | None = None,
) -> None:
    if not pairs:
        lines.append(f"{title}: none")
        return
    lines.append(f"{title} ({len(pairs)})")
    count = 0
    for row_a, row_b, overlap, struct_score in pairs:
        if count >= max_pairs:
            break
        if same_file_counts is not None and row_a.get("file_path") == row_b.get("file_path"):
            file_key = row_a.get("file_path") or ""
            if not same_file_allowed(file_key, row_a.get("content") or "", same_file_counts):
                continue
        if dup_helpers.is_low_signal_preview(row_a.get("content") or "") and dup_helpers.is_low_signal_preview(
            row_b.get("content") or ""
        ):
            continue
        meta_a = row_a.get("metadata") or {}
        meta_b = row_b.get("metadata") or {}
        a_start = meta_a.get("start_line")
        b_start = meta_b.get("start_line")
        a_line = f":{a_start}" if isinstance(a_start, int) else ""
        b_line = f":{b_start}" if isinstance(b_start, int) else ""
        preview_a = dup_helpers.preview_line(row_a.get("content") or "")
        preview_b = dup_helpers.preview_line(row_b.get("content") or "")
        lines.append(
            f"- {row_a['file_path']}{a_line} ↔ {row_b['file_path']}{b_line}  "
            f"(score={overlap:.2f}, struct={struct_score:.2f})"
        )
        lines.append(f"  A: {preview_a}")
        lines.append(f"  B: {preview_b}")
        if same_file_counts is not None and row_a.get("file_path") == row_b.get("file_path"):
            same_file_counts[file_key] = same_file_counts.get(file_key, 0) + 1
        count += 1


def append_duplicate_symbol_names(lines: list[str], records: list[dict]) -> None:
    if not records:
        return
    lines.append("\nPotential duplicate symbol names:")
    for rec in records:
        lines.append(f"- {rec['name']}  (files={rec['count']})")
        for fp in rec["files"]:
            lines.append(f"  {fp}")
