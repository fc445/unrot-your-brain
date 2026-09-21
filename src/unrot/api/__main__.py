"""`python -m unrot.api` -- run the backend.

Serves the built frontend too when `ui/dist` exists, so a built checkout is one
command rather than two. In development you run this and `npm run dev` side by
side, and Vite proxies /api here.

Two ways to bind, and they are a real choice rather than a preference:

* `--host/--port` is the developer's version. A browser can reach it, which is
  the whole point, and so can anything else on the machine.
* `--uds` is how the Mac app runs it. There is no port to collide with, nothing
  appears on the machine's network surface, and reaching the gap graph requires
  filesystem access to a path inside a 0700 directory rather than the ability
  to open a local TCP connection.
"""

from __future__ import annotations

import argparse
import socket
import stat
import sys
from pathlib import Path

from ..capture import paths
from ..env import load_env
from .app import UI_DIST


class _DefaultSocket:
    """Sentinel for a bare `--uds` with no path after it."""

    def __repr__(self) -> str:  # pragma: no cover - only ever seen in argparse output
        return "$UNROT_HOME/run/core.sock"


DEFAULT_SOCKET = _DefaultSocket()

#: Exit code for "a core is already listening there". Kept distinct from the 2
#: that argparse and a missing dependency both use, because the supervisor has
#: to tell "you started two of me" apart from "I am broken": the first must not
#: be restarted out of, and the second should be.
ALREADY_RUNNING = 3


class CannotBind(Exception):
    """The socket cannot be bound, and the reason is the user's to fix."""


class AlreadyRunning(CannotBind):
    """Something is already answering on that socket. Not a fault; a duplicate."""


def prepare_socket(path: Path) -> Path:
    """Make `path` bindable, or refuse to.

    Three situations, and only one of them is safe to clear:

    * **Nothing there.** Normal.
    * **A socket nobody is listening on.** A previous core was killed hard
      enough not to clean up -- SIGKILL, a panic, a power cut. asyncio's
      `create_unix_server` does not unlink for you, so the next start fails
      with EADDRINUSE forever until someone deletes the file by hand. Clear it.
    * **A socket that answers.** A core is already running. Refuse, loudly. The
      alternative -- unlinking and binding anyway -- silently strands the first
      core holding an open database while a second one writes to it.

    Anything that is *not* a socket is left alone and refused. Being handed a
    path is not a licence to delete whatever happens to be at it.
    """
    # 0700 on the directory is what actually keeps other users out. Not the
    # socket's own mode: uvicorn chmods that to 0666 on bind (server.py), so
    # anything resting on the file's permissions rests on something overwritten
    # a moment later.
    paths.ensure_private_dir(path.parent)

    # Darwin's sun_path is 104 bytes including the terminator, and bind() reports
    # overflow as a bare OSError that names neither the limit nor the path.
    encoded = len(str(path).encode("utf-8"))
    if encoded > paths.SUN_PATH_MAX:
        raise CannotBind(
            f"socket path is {encoded} bytes; the system limit is"
            f" {paths.SUN_PATH_MAX}.\n  {path}\nSet $UNROT_HOME to somewhere shorter."
        )

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return path

    if not stat.S_ISSOCK(mode):
        raise CannotBind(
            f"{path} exists and is not a socket. Refusing to remove it -- move it"
            " aside yourself if it is really rubbish."
        )

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        probe.connect(str(path))
    except (ConnectionRefusedError, FileNotFoundError):
        path.unlink(missing_ok=True)
        return path
    except OSError as exc:
        raise CannotBind(f"{path} is a socket but did not behave like one: {exc}") from exc
    else:
        raise AlreadyRunning(
            f"a core is already listening on {path}.\nStop it first, or point"
            " $UNROT_HOME somewhere else."
        )
    finally:
        probe.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="unrot.api", description=__doc__)
    parser.add_argument("--host", default=None, help="default 127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="default 8000")
    parser.add_argument(
        "--uds",
        nargs="?",
        const=DEFAULT_SOCKET,
        default=None,
        metavar="PATH",
        help="bind a Unix socket instead of a TCP port."
        " Bare --uds uses $UNROT_HOME/run/core.sock",
    )
    parser.add_argument("--reload", action="store_true", help="reload on code changes")
    args = parser.parse_args(argv)

    # Not an either/or that resolves by precedence: a caller who passed both has
    # a wrong idea about what is about to happen, and silently honouring one of
    # them leaves them with it.
    if args.uds is not None and (args.host is not None or args.port is not None):
        parser.error("--uds binds a socket and --host/--port bind a TCP port; pick one")

    sock_path: Path | None = None
    if args.uds is not None:
        sock_path = (
            paths.socket_path(paths.home())
            if isinstance(args.uds, _DefaultSocket)
            else Path(args.uds).expanduser()
        )
        try:
            prepare_socket(sock_path)
        except CannotBind as exc:
            print(f"unrot.api: {exc}", file=sys.stderr)
            return ALREADY_RUNNING if isinstance(exc, AlreadyRunning) else 2

    # Before uvicorn starts, so the grader sees the key. Without this the
    # server falls back to keyword grading for every answer and reports it as
    # ungraded-by-model -- true, but for a reason nobody would guess.
    loaded = load_env()
    if loaded:
        print(f"config: {loaded[0]}")

    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn is not installed. Run: uv sync", file=sys.stderr
        )
        return 2

    if not UI_DIST.is_dir():
        print(
            f"No built frontend at {UI_DIST} -- serving the API only."
            "\nFor the UI: cd ui && npm install && npm run dev  (then open :5173)"
        )

    if sock_path is not None:
        # uvicorn unlinks the socket in its own `finally`, so the only leak is a
        # process killed hard enough to skip it -- which is exactly what
        # `prepare_socket` cleans up on the way back in.
        uvicorn.run("unrot.api.app:app", uds=str(sock_path), reload=args.reload)
    else:
        uvicorn.run(
            "unrot.api.app:app",
            host=args.host or "127.0.0.1",
            port=args.port or 8000,
            reload=args.reload,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
