"""Nonblocking OS lock held for one mutable embedded database owner's lifetime."""
import os
from pathlib import Path


class EmbeddedOwnerLock:
    def __init__(self, database_path: str):
        path = Path(database_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = str(path) + '.owner.lock'
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(self._fd).st_size == 0:
                    os.write(self._fd, b'0')
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as exc:
            os.close(self._fd)
            self._fd = None
            if not isinstance(exc, OSError):
                raise
            raise RuntimeError('Embedded database already has an owner or ownership is unavailable') from exc

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        # Keep the lock inode: unlinking it permits two different owners.
