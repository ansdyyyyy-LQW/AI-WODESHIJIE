from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


def _try_lock(handle: BinaryIO) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, IOError):
        return False


def _unlock(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (OSError, IOError):
        pass


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[bool]:
    """Non-blocking cross-process lock. The file remains as a harmless lock anchor."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    acquired = _try_lock(handle)
    try:
        yield acquired
    finally:
        if acquired:
            _unlock(handle)
        handle.close()
