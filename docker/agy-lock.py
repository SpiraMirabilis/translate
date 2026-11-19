"""Hold the sign-in lock until the direct agy process exits."""
import fcntl
import os
import signal
import stat
import subprocess
import sys
from collections import deque


def run_locked(path, command):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)

        # Forward signals outside the handler so they cannot interrupt child reaping.
        pending = deque()

        def defer(signum, _frame):
            pending.append(signum)

        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
            signal.signal(sig, defer)

        with subprocess.Popen(command, close_fds=True) as child:
            while True:
                while pending:
                    child.send_signal(pending.popleft())
                try:
                    status = child.wait(timeout=0.1)
                    return 128 - status if status < 0 else status
                except subprocess.TimeoutExpired:
                    pass
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        sys.exit(run_locked(sys.argv[1], sys.argv[2:]))
    except OSError as exc:
        print(f"agy wrapper: {exc}", file=sys.stderr)
        sys.exit(1)
