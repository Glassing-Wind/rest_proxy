"""Ownership records for structural staging graphs; uncertain writers stay protected."""

import os
import socket
import threading
import uuid


class ShadowLifecycle:
    """Track a writer through parsing, finalization and publication, including heartbeats."""

    def __init__(self, driver, database: str, project_id: str, namespace: str, run_id: str):
        self.driver = driver
        self.database = database
        self.project_id = project_id
        self.namespace = namespace
        self.run_id = run_id
        self.owner = uuid.uuid4().hex
        self.stop = threading.Event()
        self.thread = None

    def _write(self, query: str, **params) -> None:
        from neo4j import unit_of_work

        @unit_of_work(timeout=30, metadata={"source": "lm_proxy", "op": "shadow_lifecycle"})
        def write(tx):
            tx.run(query, namespace=self.namespace, owner=self.owner, **params).consume()

        with self.driver.session(database=self.database) as session:
            session.execute_write(write)

    def __enter__(self):
        self._write(
            "CREATE (s:ShadowRun {namespace:$namespace, owner:$owner, "
            "project_id:$project, run_id:$run, host:$host, pid:$pid, status:'running', "
            "started_at:timestamp(), heartbeat_at:timestamp()})",
            project=self.project_id, run=self.run_id, host=socket.gethostname(), pid=os.getpid(),
        )
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.thread.start()
        return self

    def _heartbeat(self):
        while not self.stop.wait(15):
            try:
                self._write(
                    "MATCH (s:ShadowRun {namespace:$namespace, owner:$owner}) "
                    "WHERE s.status = 'running' SET s.heartbeat_at = timestamp()"
                )
            except Exception:
                # Missing heartbeat never grants cleanup permission.
                continue

    def __exit__(self, exc_type, exc, tb):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=35)
        try:
            self._write(
                "MATCH (s:ShadowRun {namespace:$namespace, owner:$owner}) "
                "SET s.status=$status, s.finished_at=timestamp(), s.heartbeat_at=timestamp()",
                status="failed" if exc_type else "finished",
            )
        except Exception as terminal_error:
            # Leave protected ownership on an uncertain completion.
            from graphrag_core.indexing.failure_evidence import record_index_failure
            record_index_failure(self.project_id, self.run_id, "lifecycle_terminal_write", terminal_error)
        return False
