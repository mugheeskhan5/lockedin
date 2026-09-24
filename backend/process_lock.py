"""Local OS locks released on process exit, including a crash. Keep lock files."""
import errno
import os
from contextlib import contextmanager
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data'


@contextmanager
def process_lock(name):
    DATA.mkdir(parents=True, exist_ok=True)
    # Open without truncating: another process may hold this exact inode/byte.
    with (DATA / (name + '.lock')).open('a+b') as handle:
        acquired = False
        if os.name == 'nt':
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
        try:
            yield acquired
        finally:
            if acquired:
                if os.name == 'nt':
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
