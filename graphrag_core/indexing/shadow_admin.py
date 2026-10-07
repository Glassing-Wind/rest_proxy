"""Explicit, evidence-backed adjudication of legacy staging graphs."""

import hashlib
import json


def snapshot_shadow(tx, namespace: str) -> dict:
    """Capture nodes and every incident edge, retaining boundary endpoint identities."""
    nodes = tx.run(
        "MATCH (n {project_id:$ns}) RETURN elementId(n) AS eid, labels(n) AS labels, "
        "properties(n) AS properties ORDER BY eid", ns=namespace,
    ).data()
    edges = tx.run(
        "MATCH (a)-[r]->(b) WHERE a.project_id=$ns OR b.project_id=$ns OR r.project_id=$ns "
        "RETURN elementId(r) AS eid, elementId(a) AS start, elementId(b) AS end, "
        "type(r) AS type, properties(r) AS properties, "
        "a.project_id=$ns AND b.project_id=$ns AS internal ORDER BY eid", ns=namespace,
    ).data()
    return {"namespace": namespace, "nodes": nodes, "relationships": edges}


def snapshot_digest(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()


def adjudicate_and_delete(tx, *, namespace: str, failed_run: str, superseding_run: str,
                          snapshot: dict, evidence: dict, decision_id: str) -> dict:
    """Recheck evidence and staging contents under a project lock, then atomically delete."""
    project, separator, parsed_run = namespace.partition("::shadow::")
    if not separator or parsed_run != failed_run or not project:
        raise ValueError("Namespace does not match the failed run")
    row = tx.run(
        "MATCH (p:Project {id:$project}) "
        "SET p.shadow_admin_lock=coalesce(p.shadow_admin_lock,0)+1 "
        "WITH p MATCH (old:IndexRun {id:$failed,project_id:$project,phase:'struct'}) "
        "MATCH (new:IndexRun {id:$new,project_id:$project,phase:'struct'}) "
        "WHERE p.struct_index_status='done' AND p.struct_active_run_id=$new "
        "AND new.status='done' AND new.promoted_at IS NOT NULL "
        "AND new.started_at > old.started_at AND old.promoted_at IS NULL "
        "AND old.started_at >= $failed_started AND old.started_at <= $failed_finished "
        "AND new.started_at >= $new_started AND new.started_at <= $new_finished "
        "AND old.status IN ['struct_written','failed','finalize_failed'] "
        "AND NOT EXISTS { MATCH (s:ShadowRun) WHERE s.namespace=$ns "
        "OR (s.project_id=$project AND s.status='running') } "
        "RETURN old.id AS failed", project=project, failed=failed_run,
        new=superseding_run, ns=namespace,
        failed_started=int(evidence["failed"]["started_at"] * 1000),
        failed_finished=int(evidence["failed"]["finished_at"] * 1000),
        new_started=int(evidence["superseding"]["started_at"] * 1000),
        new_finished=int(evidence["superseding"]["finished_at"] * 1000),
    ).single()
    if not row:
        raise RuntimeError("Legacy adjudication refused: ownership, activity or publication changed")
    current = snapshot_shadow(tx, namespace)
    if not current["nodes"] or snapshot_digest(current) != snapshot_digest(snapshot):
        raise RuntimeError("Legacy adjudication refused: staging contents changed")
    if any(n["properties"].get("last_seen_run") != failed_run for n in current["nodes"]):
        raise RuntimeError("Legacy adjudication refused: mixed or unknown run provenance")
    # Only exact staging nodes and tagged/incident edges are removed; boundary
    # endpoint nodes are preserved. The decision remains separate from run history.
    tx.run(
        "MATCH (a)-[r]->(b) WHERE a.project_id=$ns OR b.project_id=$ns OR r.project_id=$ns "
        "DELETE r", ns=namespace,
    ).consume()
    tx.run("MATCH (n {project_id:$ns}) DELETE n", ns=namespace).consume()
    tx.run(
        "CREATE (:ShadowAdjudication {id:$id,namespace:$ns,project_id:$project, "
        "failed_run:$failed,superseding_run:$new,decided_at:timestamp(), "
        "snapshot_sha256:$digest,evidence:$evidence,decision:'deleted_abandoned_legacy', "
        "nodes_deleted:$nodes,relationships_deleted:$rels})",
        id=decision_id, ns=namespace, project=project, failed=failed_run,
        new=superseding_run, digest=snapshot_digest(snapshot),
        evidence=json.dumps(evidence, sort_keys=True), nodes=len(current["nodes"]),
        rels=len(current["relationships"]),
    ).consume()
    return {"decision_id": decision_id, "nodes_deleted": len(current["nodes"]),
            "relationships_deleted": len(current["relationships"])}
