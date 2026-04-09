"""Pure helpers for semantic search result processing."""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def duplicate_experiment_flags_from_env() -> dict:
    stage = (os.getenv("LM_PROXY_DUPLICATE_ROLLOUT_STAGE") or "stage2").strip().lower()
    raw = (os.getenv("LM_PROXY_DUPLICATE_EXPERIMENTS") or "").strip()
    flags = {
        "boilerplate_variant_suppression": False,
        "canonical_docs_mirror_suppression": False,
        "helper_clone_suppression": False,
        "threshold_struct": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_STRUCT"),
        "threshold_lexical": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_LEXICAL"),
        "threshold_role": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_ROLE"),
        "min_length_ratio": _float_env("LM_PROXY_DUPLICATE_MIN_LENGTH_RATIO"),
        "max_length_ratio": _float_env("LM_PROXY_DUPLICATE_MAX_LENGTH_RATIO"),
        "threshold_query_distinction": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_QUERY_DISTINCTION"),
        "allow_cross_role_suppression": False,
    }
    stage_map = {
        "off": {},
        "none": {},
        "exact_only": {},
        "stage1": {"boilerplate_variant_suppression": True},
        "stage2": {
            "boilerplate_variant_suppression": True,
            "canonical_docs_mirror_suppression": True,
        },
        "stage3": {
            "boilerplate_variant_suppression": True,
            "canonical_docs_mirror_suppression": True,
            "helper_clone_suppression": True,
        },
    }
    for key, value in stage_map.get(stage, {}).items():
        flags[key] = value
    if not raw:
        return flags
    enabled = {
        token.strip().lower()
        for token in raw.split(",")
        if token.strip()
    }
    if "boilerplate" in enabled or "boilerplate_variant_suppression" in enabled:
        flags["boilerplate_variant_suppression"] = True
    if "canonical_docs_mirror" in enabled or "canonical_docs_mirror_suppression" in enabled:
        flags["canonical_docs_mirror_suppression"] = True
    if "helper_clone" in enabled or "helper_clone_suppression" in enabled:
        flags["helper_clone_suppression"] = True
    return flags


def duplicate_telemetry_enabled() -> bool:
    raw = os.getenv("LM_PROXY_DUPLICATE_TELEMETRY", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def append_duplicate_telemetry_event(
    trace: dict,
    *,
    query: str,
    tool: str,
    mode: str,
    topic: str = "",
) -> None:
    if not duplicate_telemetry_enabled() or not isinstance(trace, dict):
        return
    path = os.getenv("LM_PROXY_DUPLICATE_TELEMETRY_PATH", "").strip()
    if path:
        target = Path(os.path.expanduser(path))
    else:
        target = Path(__file__).resolve().parents[3] / ".runtime" / "duplicate_telemetry.ndjson"
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "mode": mode,
        "topic": topic,
        "query": (query or "")[:500],
        "selection": trace.get("selection", {}),
        "telemetry": trace.get("telemetry", {}),
        "suppression_policy": trace.get("suppression_policy", "exact_only"),
        "experiments": trace.get("experiments", {}),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
    except Exception:
        return


def _float_env(name: str) -> float | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _context_payload(results: list[dict]) -> str:
    payload: list[dict] = []
    for result in results:
        payload.append(
            {
                "file_path": result.get("file_path") or "",
                "metadata": coerce_meta(result),
            }
        )
    return json.dumps(payload)


def _load_ts_pack():
    try:
        import tree_sitter_language_pack as ts_pack
    except Exception:
        return None
    return ts_pack


def _ts_pack_runtime_call(
    function_name: str,
    *,
    texts: list[str],
    relevance_scores: list[float] | None = None,
    query: str = "",
    mode: str = "code",
    contexts_json: str = "[]",
    experiments: dict | None = None,
):
    try:
        from _runtime import resolve_python_runtime
    except Exception:
        return None
    runtime = resolve_python_runtime()
    python_cmd = list(runtime.get("cmd") or [])
    if not python_cmd:
        return None
    payload = {
        "function": function_name,
        "texts": texts,
        "relevance_scores": relevance_scores or [],
        "query": query or None,
        "mode": mode,
        "contexts_json": contexts_json,
        "experiments": experiments or {},
    }
    script = """
import json, sys
import tree_sitter_language_pack as ts_pack
payload = json.loads(sys.stdin.read())
fn = payload["function"]
if fn == "analyze_duplicate_texts":
    result = ts_pack.analyze_duplicate_texts(
        payload["texts"],
        payload.get("query"),
        payload.get("mode"),
        payload.get("contexts_json"),
    )
elif fn == "rerank_diverse_texts":
    result = ts_pack.rerank_diverse_texts(
        payload["texts"],
        payload.get("relevance_scores") or [],
        payload.get("query"),
        payload.get("mode"),
        payload.get("contexts_json"),
    )
elif fn == "trace_diverse_texts":
    result = ts_pack.trace_diverse_texts(
        payload["texts"],
        payload.get("relevance_scores") or [],
        payload.get("query"),
        payload.get("mode"),
        payload.get("contexts_json"),
        json.dumps(payload.get("experiments") or {}),
    )
else:
    raise SystemExit(f"unknown function: {fn}")
print(json.dumps(result))
"""
    try:
        completed = subprocess.run(
            python_cmd + ["-c", script],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return None
    try:
        return json.loads(completed.stdout)
    except Exception:
        return None


def collapse_near_duplicate_results(results: list[dict], *, query: str = "", mode: str = "code") -> list[dict]:
    if len(results) < 2:
        return results
    selection = rerank_diverse_results(results, query=query, mode=mode)
    keep_indices = selection.get("keep_indices")
    if not isinstance(keep_indices, list):
        return results
    keep_set = {
        idx
        for idx in keep_indices
        if isinstance(idx, int) and 0 <= idx < len(results)
    }
    if not keep_set:
        return results
    return [results[idx] for idx in keep_indices if idx in keep_set]


def trace_diverse_results(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
    experiments: dict | None = None,
) -> dict:
    if len(results) < 2:
        keep = list(range(len(results)))
        mode_name = "docs_retrieval" if mode == "docs" else "code_retrieval"
        return {
            "selection": {
                "mode": mode_name,
                "keep_indices": keep,
                "suppressed_indices": [],
                "exact_suppressed_indices": [],
                "group_order": keep,
                "representative_indices": keep,
                "mmr_lambda": 0.78,
                "aspect_lambda": 0.18,
                "selected_aspects": [],
            },
            "candidates": [],
            "telemetry": {
                "mode": mode_name,
                "query_class": "unknown",
                "exact_suppressions": 0,
                "experimental_suppressions": 0,
                "relation_counts": {},
                "group_sizes": [],
                "representative_selection_reasons": {},
                "topk_redundancy_before": 0.0,
                "topk_redundancy_after": 0.0,
                "kept_group_multi_member_count": 0,
                "canonical_doc_preference_success": None,
                "version_sensitive_query": False,
            },
            "suppression_policy": "exact_only",
            "experiments": experiments or {},
        }
    ts_pack = _load_ts_pack()

    trace = getattr(ts_pack, "trace_diverse_texts", None) if ts_pack is not None else None
    if not callable(trace):
        payload = experiments if isinstance(experiments, dict) else duplicate_experiment_flags_from_env()
        fallback = _ts_pack_runtime_call(
            "trace_diverse_texts",
            texts=[
                result.get("content") if isinstance(result.get("content"), str) else ""
                for result in results
            ],
            relevance_scores=[
                float(result.get("rrf", 0.0)) if isinstance(result.get("rrf", 0.0), (int, float)) else 0.0
                for result in results
            ],
            query=query,
            mode=mode,
            contexts_json=_context_payload(results),
            experiments=payload,
        )
        if isinstance(fallback, dict):
            return fallback
        selection = rerank_diverse_results(results, query=query, mode=mode)
        return {
            "selection": selection,
            "candidates": [],
            "telemetry": {
                "mode": selection.get("mode", "code_retrieval"),
                "query_class": "unknown",
                "exact_suppressions": len(selection.get("exact_suppressed_indices", [])),
                "experimental_suppressions": 0,
                "relation_counts": {},
                "group_sizes": [],
                "representative_selection_reasons": {},
                "topk_redundancy_before": 0.0,
                "topk_redundancy_after": 0.0,
                "kept_group_multi_member_count": 0,
                "canonical_doc_preference_success": None,
                "version_sensitive_query": False,
            },
            "suppression_policy": "exact_only",
            "experiments": payload,
        }

    texts: list[str] = []
    relevance_scores: list[float] = []
    for result in results:
        content = result.get("content")
        texts.append(content if isinstance(content, str) else "")
        try:
            relevance_scores.append(float(result.get("rrf", 0.0)))
        except (TypeError, ValueError):
            relevance_scores.append(0.0)

    payload = experiments if isinstance(experiments, dict) else duplicate_experiment_flags_from_env()
    try:
        traced = trace(
            texts,
            relevance_scores,
            query or None,
            mode,
            _context_payload(results),
            json.dumps(payload),
        )
    except Exception:
        selection = rerank_diverse_results(results, query=query, mode=mode)
        return {
            "selection": selection,
            "candidates": [],
            "telemetry": {
                "mode": selection.get("mode", "code_retrieval"),
                "query_class": "unknown",
                "exact_suppressions": len(selection.get("exact_suppressed_indices", [])),
                "experimental_suppressions": 0,
                "relation_counts": {},
                "group_sizes": [],
                "representative_selection_reasons": {},
                "topk_redundancy_before": 0.0,
                "topk_redundancy_after": 0.0,
                "kept_group_multi_member_count": 0,
                "canonical_doc_preference_success": None,
                "version_sensitive_query": False,
            },
            "suppression_policy": "exact_only",
            "experiments": payload,
        }
    if isinstance(traced, dict):
        return traced
    return trace_diverse_results(results[:1], query=query, mode=mode, experiments=payload)


def analyze_near_duplicate_results(results: list[dict], *, query: str = "", mode: str = "code") -> dict:
    if len(results) < 2:
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "pairs": [],
            "groups": [],
        }
    ts_pack = _load_ts_pack()

    analyze = getattr(ts_pack, "analyze_duplicate_texts", None) if ts_pack is not None else None
    if not callable(analyze):
        fallback = _ts_pack_runtime_call(
            "analyze_duplicate_texts",
            texts=[
                result.get("content") if isinstance(result.get("content"), str) else ""
                for result in results
            ],
            query=query,
            mode=mode,
            contexts_json=_context_payload(results),
        )
        if isinstance(fallback, dict):
            return fallback
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "pairs": [],
            "groups": [],
        }

    texts: list[str] = []
    for result in results:
        content = result.get("content")
        texts.append(content if isinstance(content, str) else "")

    try:
        analysis = analyze(texts, query or None, mode, _context_payload(results))
    except Exception:
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "pairs": [],
            "groups": [],
        }

    return analysis if isinstance(analysis, dict) else {
        "mode": "code_retrieval",
        "keep_indices": list(range(len(results))),
        "suppressed_indices": [],
        "pairs": [],
    }


def rerank_diverse_results(results: list[dict], *, query: str = "", mode: str = "code") -> dict:
    if len(results) < 2:
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "exact_suppressed_indices": [],
            "group_order": list(range(len(results))),
            "representative_indices": list(range(len(results))),
        }
    ts_pack = _load_ts_pack()
    if ts_pack is None:
        fallback = _ts_pack_runtime_call(
            "rerank_diverse_texts",
            texts=[
                result.get("content") if isinstance(result.get("content"), str) else ""
                for result in results
            ],
            relevance_scores=[
                float(result.get("rrf", 0.0)) if isinstance(result.get("rrf", 0.0), (int, float)) else 0.0
                for result in results
            ],
            query=query,
            mode=mode,
            contexts_json=_context_payload(results),
        )
        if isinstance(fallback, dict):
            return fallback
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
        }

    rerank = getattr(ts_pack, "rerank_diverse_texts", None)
    if not callable(rerank):
        analysis = analyze_near_duplicate_results(results, query=query, mode=mode)
        return {
            "mode": analysis.get("mode", "code_retrieval"),
            "keep_indices": analysis.get("keep_indices", list(range(len(results)))),
            "suppressed_indices": analysis.get("suppressed_indices", []),
            "exact_suppressed_indices": analysis.get("suppressed_indices", []),
            "group_order": [],
            "representative_indices": analysis.get("keep_indices", list(range(len(results)))),
        }

    texts: list[str] = []
    relevance_scores: list[float] = []
    for result in results:
        content = result.get("content")
        texts.append(content if isinstance(content, str) else "")
        try:
            relevance_scores.append(float(result.get("rrf", 0.0)))
        except (TypeError, ValueError):
            relevance_scores.append(0.0)

    try:
        selection = rerank(texts, relevance_scores, query or None, mode, _context_payload(results))
    except Exception:
        analysis = analyze_near_duplicate_results(results, query=query, mode=mode)
        return {
            "mode": analysis.get("mode", "code_retrieval"),
            "keep_indices": analysis.get("keep_indices", list(range(len(results)))),
            "suppressed_indices": analysis.get("suppressed_indices", []),
            "exact_suppressed_indices": analysis.get("suppressed_indices", []),
            "group_order": [],
            "representative_indices": analysis.get("keep_indices", list(range(len(results)))),
        }

    return selection if isinstance(selection, dict) else {
        "mode": "code_retrieval",
        "keep_indices": list(range(len(results))),
        "suppressed_indices": [],
        "exact_suppressed_indices": [],
        "group_order": list(range(len(results))),
        "representative_indices": list(range(len(results))),
    }


def summarize_trace_for_debug(trace: dict) -> list[str]:
    selection = trace.get("selection") if isinstance(trace, dict) else {}
    telemetry = trace.get("telemetry") if isinstance(trace, dict) else {}
    lines = [
        "duplicate trace:",
        f"- suppression_policy={trace.get('suppression_policy', 'exact_only')}",
        f"- keep={selection.get('keep_indices', [])}",
        f"- exact_suppressed={selection.get('exact_suppressed_indices', [])}",
        f"- experimental_suppressed={telemetry.get('experimental_suppressions', 0)}",
        f"- query_class={telemetry.get('query_class', 'unknown')}",
        f"- topk_redundancy_before={telemetry.get('topk_redundancy_before', 0.0):.3f}",
        f"- topk_redundancy_after={telemetry.get('topk_redundancy_after', 0.0):.3f}",
        f"- multi_rep_groups={telemetry.get('multi_representative_group_count', 0)}",
        f"- query_distinct_multi_rep={telemetry.get('query_distinct_multi_rep_count', 0)}",
        f"- alerts={','.join(telemetry.get('regression_alerts', []) or []) or '(none)'}",
    ]
    candidates = trace.get("candidates") if isinstance(trace, dict) else []
    if isinstance(candidates, list):
        for candidate in candidates[:6]:
            if not isinstance(candidate, dict):
                continue
            lines.append(
                "- idx={idx} group={group} kept={kept} reason={reason} beat_by={beat} qdist={qdist:.3f} rels={rels}".format(
                    idx=candidate.get("idx"),
                    group=candidate.get("group_id"),
                    kept=candidate.get("kept"),
                    reason=candidate.get("decision_reason"),
                    beat=candidate.get("beaten_by"),
                    qdist=float(candidate.get("query_distinction_score", 0.0)),
                    rels=",".join(candidate.get("duplicate_relations") or []),
                )
            )
    return lines


def format_meta(meta: dict) -> list[str]:
    if not isinstance(meta, dict):
        return []
    parts: list[str] = []
    language = meta.get("language")
    if language:
        parts.append(f"lang={language}")
    imports = meta.get("file_imports")
    if isinstance(imports, list) and imports:
        parts.append(f"imports={len(imports)}")
    symbols = meta.get("file_symbols")
    if isinstance(symbols, list) and symbols:
        parts.append(f"symbols={len(symbols)}")
    node_types = meta.get("node_types")
    if isinstance(node_types, list) and node_types:
        parts.append(f"node_types={len(node_types)}")
    diagnostics = meta.get("file_diagnostics") or {}
    if isinstance(diagnostics, dict):
        diag_count = diagnostics.get("count")
        if isinstance(diag_count, int) and diag_count > 0:
            parts.append(f"diagnostics={diag_count}")
    metrics = meta.get("file_metrics") or {}
    if isinstance(metrics, dict):
        total_lines = metrics.get("total_lines")
        if isinstance(total_lines, int):
            parts.append(f"lines={total_lines}")
    cargo_crate = meta.get("cargo_crate")
    if cargo_crate:
        parts.append(f"crate={cargo_crate}")
    ctx = meta.get("context_path")
    ctx_line = ""
    if isinstance(ctx, list) and ctx:
        ctx_line = "context=" + " > ".join(str(c) for c in ctx[:6])
    output = []
    if parts:
        output.append("meta: " + ", ".join(parts))
    if ctx_line:
        output.append(ctx_line)
    return output


def cargo_manifest_dir(manifest_path: str | None) -> str:
    if not manifest_path:
        return ""
    return manifest_path[:-len("Cargo.toml")] if manifest_path.endswith("Cargo.toml") else manifest_path


def match_cargo_crate(file_path: str | None, crate_rows) -> tuple[str | None, str | None]:
    if not file_path:
        return None, None
    for row in crate_rows:
        manifest_path = row.get("manifest_path")
        crate_root = cargo_manifest_dir(manifest_path)
        if crate_root and file_path.startswith(crate_root):
            return row.get("crate"), row.get("crate_name")
    return None, None


def attach_cargo_crate_meta(results: list[dict], crate_rows) -> list[dict]:
    if not crate_rows:
        return results
    for result in results:
        meta = coerce_meta(result)
        crate, crate_name = match_cargo_crate(result.get("file_path"), crate_rows)
        if crate:
            meta["cargo_crate"] = crate
        if crate_name:
            meta["cargo_crate_name"] = crate_name
    return results


def filter_by_cargo_crate(results: list[dict], crate_contains: str) -> list[dict]:
    needle = (crate_contains or "").strip().lower()
    if not needle:
        return results
    filtered: list[dict] = []
    for result in results:
        meta = coerce_meta(result)
        crate = meta.get("cargo_crate")
        crate_name = meta.get("cargo_crate_name")
        if (crate and needle in str(crate).lower()) or (
            crate_name and needle in str(crate_name).lower()
        ):
            filtered.append(result)
    return filtered


def meta_score(meta: dict) -> int:
    if not isinstance(meta, dict):
        return 0
    score = 0
    for key in (
        "file_imports",
        "file_symbols",
        "node_types",
        "file_metrics",
        "file_diagnostics",
        "context_path",
    ):
        value = meta.get(key)
        if isinstance(value, list) and value:
            score += 1
        elif isinstance(value, dict) and value:
            score += 1
    return score


def passes_filters(
    meta: dict,
    *,
    languages,
    min_imports: int,
    min_symbols: int,
    require_diagnostics: bool,
    require_context: bool,
) -> bool:
    if not isinstance(meta, dict):
        return False
    if languages:
        language = meta.get("language")
        if not language or language not in languages:
            return False
    if min_imports > 0:
        imports = meta.get("file_imports")
        if not isinstance(imports, list) or len(imports) < min_imports:
            return False
    if min_symbols > 0:
        symbols = meta.get("file_symbols")
        if not isinstance(symbols, list) or len(symbols) < min_symbols:
            return False
    if require_diagnostics:
        diagnostics = meta.get("file_diagnostics") or {}
        if not isinstance(diagnostics, dict) or diagnostics.get("count", 0) <= 0:
            return False
    if require_context:
        ctx = meta.get("context_path")
        if not isinstance(ctx, list) or not ctx:
            return False
    return True


def path_allowed(file_path: str, *, include_paths, exclude_paths) -> bool:
    if not file_path:
        return True
    if include_paths and not any(fnmatch.fnmatch(file_path, pat) for pat in include_paths):
        return False
    if exclude_paths:
        file_lower = file_path.lower()
        if any(
            fnmatch.fnmatch(file_path, pat) or fnmatch.fnmatch(file_lower, pat.lower())
            for pat in exclude_paths
        ):
            return False
    return True


def coerce_meta(result: dict) -> dict:
    meta = result.get("_meta")
    if isinstance(meta, dict):
        return meta
    raw = result.get("metadata")
    if isinstance(raw, str):
        try:
            meta = json.loads(raw)
        except Exception:
            meta = {}
    elif isinstance(raw, dict):
        meta = raw
    else:
        meta = {}
    result["_meta"] = meta
    return meta


def dedupe_files(results: list[dict]) -> list[dict]:
    seen_files: set[str] = set()
    deduped: list[dict] = []
    for result in results:
        file_path = result.get("file_path")
        if not file_path or file_path in seen_files:
            continue
        seen_files.add(file_path)
        deduped.append(result)
    return deduped


def cap_per_file(results: list[dict], max_per_file: int) -> list[dict]:
    if max_per_file <= 0:
        return results
    per_file_counts: dict[str, int] = {}
    capped: list[dict] = []
    for result in results:
        file_path = result.get("file_path") or ""
        if not file_path:
            continue
        count = per_file_counts.get(file_path, 0)
        if count >= max_per_file:
            continue
        per_file_counts[file_path] = count + 1
        capped.append(result)
    return capped


def cap_per_dir(results: list[dict], max_per_dir: int) -> list[dict]:
    if max_per_dir <= 0:
        return results
    dir_counts: dict[str, int] = {}
    diversified: list[dict] = []
    for result in results:
        file_path = result.get("file_path") or ""
        norm = file_path.replace("\\", "/")
        top = norm.split("/")[0] if "/" in norm else os.path.dirname(norm) or "."
        if dir_counts.get(top, 0) >= max_per_dir:
            continue
        dir_counts[top] = dir_counts.get(top, 0) + 1
        diversified.append(result)
    return diversified


def render_results(
    results: list[dict],
    *,
    query: str,
    k: int,
    multi: bool,
    pid_to_name: dict[str, str],
    include_metadata: bool,
) -> list[str]:
    top = results[:k]
    lines: list[str] = []
    if multi:
        lines.append(f"Cross-project search: '{query}'  ({len(pid_to_name)} projects)\n")
    for index, result in enumerate(top, 1):
        project = pid_to_name.get(result["project_id"], result["project_id"])
        if multi:
            lines.append(f"[{index}] [{project}] {result['file_path']}  (score: {result['rrf']:.4f})")
        else:
            lines.append(f"--- {result['file_path']} (Score: {result['rrf']:.4f}) ---")
        if include_metadata:
            lines.extend(format_meta(coerce_meta(result)))
        lines.append(result["content"].strip())
        lines.append("")
    return lines
