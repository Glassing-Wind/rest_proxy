"""Bounded source evidence for active local task claims; no model or writes."""
import hashlib
import os
from pathlib import Path
import stat
import time

from memory.task_registry import TaskRegistry


class TaskDispatcher:
    """Trusted local callers only; project names are not authenticated identities."""

    def __init__(self, registry: TaskRegistry):
        self.registry = registry

    def read_source(self, project: str, task_id: str, revision: int, claim_token: str,
                    path: str, start_line: int = 1, end_line: int = 80) -> dict:
        if type(revision) is not int or revision < 1:
            raise ValueError('Require positive revision')
        if (type(start_line) is not int or type(end_line) is not int or
                not 1 <= start_line <= end_line or end_line - start_line >= 200):
            raise ValueError('Require positive range of at most 200 lines')
        if not isinstance(path, str) or not path or len(path) > 4096:
            raise ValueError('Require bounded relative source path')
        relative = Path(path)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError('Source path must remain inside workspace')
        if any(part.startswith('.') for part in relative.parts):
            raise ValueError('Hidden paths are not exposed by source dispatch')
        # Hold the same write lock as transitions so cancellation/reclaim cannot
        # interleave the claim check and read. Read is bounded and makes no writes.
        with self.registry.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task = self.registry._read(db, project, task_id)
            claim = task['claim']
            if (task['revision'] != revision or task['status'] != 'claimed' or
                    not claim or claim['token'] != claim_token or claim['expires_at'] <= time.time()):
                raise ValueError('Require current active claim')
            if 'read_source' not in task['actions']:
                raise ValueError('Task lacks source-read capability')
            # Open each component relative to its parent descriptor; reject
            # symlinks, including directory symlinks, rather than resolve-and-open.
            fd = os.open(task['workspace'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for index, part in enumerate(relative.parts):
                    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                    if index < len(relative.parts) - 1:
                        flags |= os.O_DIRECTORY
                    child = os.open(part, flags, dir_fd=fd)
                    os.close(fd)
                    fd = child
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1048576:
                    raise ValueError('Require regular source file of at most 1 MiB')
                data = bytearray()
                while len(data) <= 1048576:
                    chunk = os.read(fd, min(65536, 1048577 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                if len(data) > 1048576:
                    raise ValueError('Source grew beyond size limit')
            finally:
                os.close(fd)
            if claim['expires_at'] <= time.time():
                raise ValueError('Claim expired during source read')
        if b'\x00' in data:
            raise ValueError('Binary source not supported')
        lines = data.decode('utf-8').splitlines()
        if start_line > len(lines):
            raise ValueError('Start line beyond source')
        selected = lines[start_line - 1:end_line]
        text = '\n'.join(f'{start_line + i}: {line}' for i, line in enumerate(selected))
        if len(text.encode('utf-8')) > 16384:
            raise ValueError('Selected evidence exceeds 16 KiB; request fewer lines')
        return dict(project=project, task_id=task_id, revision=revision,
                    path=str(relative), start_line=start_line,
                    end_line=start_line + len(selected) - 1, total_lines=len(lines),
                    sha256=hashlib.sha256(data).hexdigest(), source=text,
                    observed_at=time.time(), historical=False)

    def submit_finding(self, project: str, task_id: str, revision: int,
                       claim_token: str, finding: dict) -> dict:
        """Validate cited current bytes and retain bounded evidence in submission.

        This verifies citation identity, not whether the answer follows from it.
        Source files can change after validation; future review must recheck them.
        """
        if not isinstance(finding, dict) or set(finding) != {
                'schema_version', 'answer', 'citations', 'limits'}:
            raise ValueError('Require versioned answer, citations and limits')
        if type(finding['schema_version']) is not int or finding['schema_version'] != 1:
            raise ValueError('Unsupported finding schema')
        answer = finding['answer']
        limits = finding['limits']
        citations = finding['citations']
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 4096:
            raise ValueError('Require bounded answer')
        if (not isinstance(limits, list) or len(limits) > 20 or
                any(not isinstance(value, str) or not value.strip() or len(value) > 1024 for value in limits)):
            raise ValueError('Require bounded limitation strings')
        if not isinstance(citations, list) or not 1 <= len(citations) <= 3:
            raise ValueError('Require one to three source citations')
        retained = []
        identities = set()
        for citation in citations:
            if not isinstance(citation, dict) or set(citation) != {
                    'path', 'start_line', 'end_line', 'sha256'}:
                raise ValueError('Invalid citation fields')
            digest = citation['sha256']
            if (not isinstance(digest, str) or len(digest) != 64 or
                    any(char not in '0123456789abcdef' for char in digest)):
                raise ValueError('Require SHA-256 source identity')
            evidence = self.read_source(project, task_id, revision, claim_token,
                                        citation['path'], citation['start_line'], citation['end_line'])
            if evidence['sha256'] != digest or evidence['end_line'] != citation['end_line']:
                raise ValueError('Citation source changed or range unavailable')
            identity = (evidence['path'], evidence['start_line'], evidence['end_line'])
            if identity in identities:
                raise ValueError('Duplicate citation')
            identities.add(identity)
            retained.append(evidence)
        return self.registry.submit(project, task_id, revision, claim_token, {
            **finding, 'retained_evidence': retained,
            'validation': 'source identity/range only; semantic review pending'})
