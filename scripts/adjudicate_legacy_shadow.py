#!/usr/bin/env python3
"""Preview or atomically remove one abandoned legacy namespace using local job evidence.

Run with project Python. --apply requires the digest from a prior read-only preview.
Credentials come from the configured environment, never command arguments or receipts.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
from neo4j import GraphDatabase, unit_of_work  # noqa: E402

from graphrag_core.indexing.shadow_admin import (  # noqa: E402
    adjudicate_and_delete, snapshot_digest, snapshot_shadow,
)


def load_evidence(namespace: str, failed_job: str, superseding_job: str) -> dict:
    """Accept only terminal local records linked to the exact failed worker/run."""
    from _jobs import _process_alive

    project, separator, run = namespace.partition("::shadow::")
    if not separator or not run.startswith(project + ":"):
        raise ValueError("Invalid legacy namespace")
    evidence = {}
    for role, jid in (("failed", failed_job), ("superseding", superseding_job)):
        if not jid or any(c not in "0123456789abcdef" for c in jid):
            raise ValueError("Invalid job ID")
        path = ROOT / ".runtime/jobs" / jid / "state.json"
        job = json.loads(path.read_text())
        if job.get("project_id") != project or job.get("job_id") != jid:
            raise ValueError("Job scope mismatch")
        required_status = "failed" if role == "failed" else "done"
        if job.get("status") != required_status or not job.get("finished_at"):
            raise ValueError("Job is not terminal with the required outcome")
        if role == "failed" and job.get("struct_rc") in (None, 0):
            raise ValueError("Failed structural exit not established")
        if role == "superseding" and job.get("struct_rc") != 0:
            raise ValueError("Successful structural exit not established")
        if any(_process_alive(job.get(key)) for key in ("struct_pid", "sem_pid")):
            raise ValueError("A recorded worker PID is still alive or has been reused")
        log = path.parent / "struct.log"
        if role == "failed" and not run.startswith(f"{project}:{job.get('struct_pid')}:"):
            raise ValueError("Failed run and worker identity do not match")
        if role == "superseding" and "[ts-pack:shadow] Promoted" not in log.read_text():
            raise ValueError("Successful publication evidence is missing")
        evidence[role] = {"job_id": jid, "state_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                          "struct_pid": job["struct_pid"], "started_at": job["started_at"],
                          "finished_at": job["finished_at"], "project_path": job["project_path"]}
    if evidence["superseding"]["started_at"] <= evidence["failed"]["finished_at"]:
        raise ValueError("Successful job does not supersede the failure")
    for path in (ROOT / ".runtime/jobs").glob("*/state.json"):
        job = json.loads(path.read_text())
        if job.get("project_id") == project and job.get("status") in {"running", "cancelling"}:
            raise ValueError("An active persisted project job exists")
    return evidence


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--failed-job", required=True)
    parser.add_argument("--superseding-job", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-digest")
    args = parser.parse_args()
    if args.apply and not args.expected_digest:
        parser.error("--apply requires --expected-digest from a prior preview")
    evidence = load_evidence(args.namespace, args.failed_job, args.superseding_job)
    project, _, failed_run = args.namespace.partition("::shadow::")
    decision = "legacy-shadow-" + uuid.uuid4().hex
    from _jobs import claim_project_job_lock, _release_project_job_lock

    # Application index starts use the same project lock. Standalone/remote
    # writers are additionally checked by the transaction's graph state guards.
    claimed = False
    if args.apply:
        claimed, _ = claim_project_job_lock(project, decision,
                                           project_path=evidence["failed"]["project_path"])
        if not claimed:
            raise RuntimeError("Project index lock is held")
    try:
        with GraphDatabase.driver(
            os.getenv("LM_PROXY_NEO4J_URI", "bolt://127.0.0.1:7687"),
            auth=(os.getenv("LM_PROXY_NEO4J_USER", "neo4j"),
                  os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")),
            user_agent=f"rest-proxy/legacy-shadow-admin pid={os.getpid()}",
        ) as driver:
            with driver.session(database=os.getenv("LM_PROXY_NEO4J_DB", "proxy")) as session:
                snapshot = session.execute_read(lambda tx: snapshot_shadow(tx, args.namespace))
                published = session.execute_read(lambda tx: tx.run(
                    "MATCH (p:Project {id:$pid}) MATCH (r:IndexRun {id:p.struct_active_run_id}) "
                    "RETURN r.id AS run,r.status AS status", pid=project,
                ).single())
                if not published or not published["run"].startswith(
                    f"{project}:{evidence['superseding']['struct_pid']}:"
                ):
                    raise RuntimeError("Superseding job is not the current published run")
                digest = snapshot_digest(snapshot)
                preview = {"namespace": args.namespace, "snapshot_sha256": digest,
                           "nodes": len(snapshot["nodes"]),
                           "relationships": len(snapshot["relationships"]),
                           "boundary_or_tagged_external": sum(not r["internal"] for r in snapshot["relationships"]),
                           "superseding_run": published["run"], "evidence": evidence}
                if args.apply:
                    # Durable local snapshot and evidence precede any deletion.
                    if digest != args.expected_digest:
                        raise RuntimeError("Preview digest no longer matches")
                    folder = ROOT / ".runtime/shadow-cleanup" / decision
                    folder.mkdir(parents=True, mode=0o700)
                    for name, payload in (("snapshot.json", snapshot), ("preview.json", preview)):
                        fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(fd, "w") as file:
                            json.dump(payload, file, sort_keys=True, default=str)
                            file.flush()
                            os.fsync(file.fileno())
                    # Recheck local activity/evidence after acquiring the index lock.
                    if load_evidence(args.namespace, args.failed_job, args.superseding_job) != evidence:
                        raise RuntimeError("Local job evidence changed")

                    @unit_of_work(timeout=120, metadata={"source": "lm_proxy", "op": "legacy_shadow_adjudication"})
                    def delete(tx):
                        return adjudicate_and_delete(
                            tx, namespace=args.namespace, failed_run=failed_run,
                            superseding_run=published["run"], snapshot=snapshot,
                            evidence=evidence, decision_id=decision,
                        )

                    preview["result"] = session.execute_write(delete)
                    preview["local_snapshot"] = str(folder / "snapshot.json")
                print(json.dumps(preview, indent=2))
        return 0
    finally:
        if claimed:
            _release_project_job_lock(project, decision)


if __name__ == "__main__":
    raise SystemExit(main())
