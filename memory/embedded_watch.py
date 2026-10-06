"""Bounded content scan for explicitly published manifest paths."""
import hashlib
from pathlib import Path


def changed_paths(root_path: str, files: list[dict]) -> tuple[bool, list[str]]:
    root = Path(root_path)
    if not root.is_dir() or root.resolve() != root:
        raise ValueError('Published watch root is missing or changed')
    if len(files) > 5000:
        raise ValueError('Watch manifest exceeds 5000 files')
    paths = []
    changed = False
    used = 0
    for entry in files:
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Watch path escapes workspace')
        path = root / relative
        try:
            path.lstat()
        except FileNotFoundError:
            changed = True
            continue
        if path.resolve() != path or not path.is_file():
            raise ValueError('Watch symlinks/non-files are unsupported')
        with path.open('rb') as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        used += len(raw)
        if len(raw) > 8 * 1024 * 1024 or used > 64 * 1024 * 1024:
            raise ValueError('Watch source budget exceeded')
        paths.append(entry['path'])
        changed |= hashlib.sha256(raw).hexdigest() != entry['sha256']
    return changed, paths
