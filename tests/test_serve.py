"""How the core binds, and what it refuses to do to get bound (PR-29 phase 0).

The Mac app supervises this process and reaches it over a Unix socket rather
than a TCP port. That choice only buys anything if two properties hold, and
both of them are easy to lose quietly:

* **Nothing else on the machine can reach the gap graph.** Which rests entirely
  on the directory being 0700 -- uvicorn chmods the socket itself to 0666.
* **A hard-killed core does not brick the next start.** asyncio does not unlink
  a stale socket, so without a reclaim step one `kill -9` leaves the product
  permanently unstartable until someone deletes a file they have never heard of.

The rest is about refusing to do damage: a path is not a licence to delete
whatever is at it.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from unrot.api.__main__ import ALREADY_RUNNING, AlreadyRunning, CannotBind, main, prepare_socket
from unrot.capture import paths

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(monkeypatch):
    """A short $UNROT_HOME, deliberately not pytest's `tmp_path`.

    On macOS `tmp_path` lands under /private/var/folders/... and is ~137 bytes
    before anything is appended -- past `sun_path`'s 104-byte limit, so every
    test here would fail on the length check rather than on what it is about.
    A real `~/.unrot/run/core.sock` is around 40 bytes, so the limit is a
    genuine edge rather than something the product sits near; it is only the
    harness that cannot fit.
    """
    root = Path(tempfile.mkdtemp(prefix="unrot-", dir="/tmp"))
    monkeypatch.setenv("UNROT_HOME", str(root))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def listening(path: Path) -> socket.socket:
    """A real listening Unix socket at `path`, closed by the caller."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.listen(1)
    return sock


def test_the_socket_directory_is_private_and_stays_that_way(home):
    """0700 is the access control. Re-applied, not just set once at creation.

    A directory created before this rule existed -- or widened by hand, or by a
    umask nobody thought about -- would otherwise stay open forever, and nothing
    about the product would look wrong.
    """
    path = paths.socket_path(home)
    path.parent.mkdir(parents=True)
    path.parent.chmod(0o755)

    prepare_socket(path)

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_socket_left_by_a_killed_core_is_reclaimed(home):
    """The `kill -9` case, which is the one that cannot be handled on the way out.

    uvicorn unlinks the socket in its own `finally`, so every ordinary exit is
    already clean. SIGKILL skips that, and asyncio's `create_unix_server` will
    not bind over the leftover -- so the reclaim has to happen on the way back
    in or the next start fails, and every start after it.
    """
    path = paths.socket_path(home)
    dead = listening(path)
    dead.close()  # the file survives the socket
    assert path.exists()

    prepare_socket(path)

    assert not path.exists()


def test_a_core_that_is_already_running_is_refused_rather_than_displaced(home):
    """Unlinking a live socket would leave two cores writing to one database.

    The first process keeps its open descriptor and its connection to
    `unrot.db`, and simply stops being reachable -- so the failure presents as
    the app working fine while an orphan writes underneath it.
    """
    path = paths.socket_path(home)
    live = listening(path)
    try:
        with pytest.raises(AlreadyRunning):
            prepare_socket(path)
        assert path.exists()
    finally:
        live.close()


def test_a_file_that_is_not_a_socket_is_never_deleted(home):
    """Being handed a path is not a licence to remove what is at it."""
    path = paths.socket_path(home)
    path.parent.mkdir(parents=True)
    path.write_text("not a socket", encoding="utf-8")

    with pytest.raises(CannotBind) as raised:
        prepare_socket(path)

    assert path.read_text(encoding="utf-8") == "not a socket"
    assert not isinstance(raised.value, AlreadyRunning)


def test_an_over_long_path_is_explained_rather_than_hit_at_bind(home):
    """`sun_path` is 104 bytes on Darwin and bind() names neither limit nor path."""
    path = home / ("d" * 40) / ("e" * 40) / ("f" * 40) / "core.sock"
    assert len(str(path)) > paths.SUN_PATH_MAX

    with pytest.raises(CannotBind) as raised:
        prepare_socket(path)

    assert str(paths.SUN_PATH_MAX) in str(raised.value)
    assert not isinstance(raised.value, AlreadyRunning)


def test_already_running_has_its_own_exit_code(home, capsys):
    """The supervisor must not restart out of this one.

    "You started two of me" and "I am broken" both end the process, and a
    supervisor that cannot tell them apart will respond to the first by starting
    a third.
    """
    path = paths.socket_path(home)
    live = listening(path)
    try:
        assert main(["--uds", str(path)]) == ALREADY_RUNNING
    finally:
        live.close()
    assert "already listening" in capsys.readouterr().err


def test_binding_a_socket_and_a_port_is_an_error_not_a_precedence_rule(home):
    """Silently honouring one leaves the caller with a wrong idea of what ran."""
    with pytest.raises(SystemExit) as raised:
        main(["--uds", "/tmp/x.sock", "--port", "9999"])
    assert raised.value.code == 2


def test_bare_uds_and_an_explicit_path_agree_on_the_default(home):
    """The app and the CLI must not each carry their own copy of the location."""
    assert paths.socket_path(home) == home / "run" / "core.sock"


# --- the actual thing ------------------------------------------------------


def request_over_socket(sock_path: Path, target: str) -> tuple[int, dict]:
    """One HTTP request over a Unix socket, which http.client can do via sock."""
    conn = http.client.HTTPConnection("localhost")
    conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.sock.settimeout(10)
    conn.sock.connect(str(sock_path))
    try:
        conn.request("GET", target)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


@pytest.mark.slow
def test_the_core_really_serves_the_surface_over_a_socket(home):
    """End to end, in a subprocess, because TestClient would prove nothing here.

    `TestClient` never touches the transport, and the transport is the entire
    change. This is the only test in the file that would have caught a uvicorn
    that quietly ignored `uds=`.
    """
    sock_path = paths.socket_path(home)
    env = os.environ | {"UNROT_HOME": str(home), "PYTHONPATH": str(REPO / "src")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "unrot.api", "--uds", str(sock_path)],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if sock_path.exists():
                try:
                    status, body = request_over_socket(sock_path, "/api/health")
                except OSError:
                    pass
                else:
                    break
            if proc.poll() is not None:
                pytest.fail(f"core exited early:\n{proc.stdout.read()}")
            time.sleep(0.2)
        else:
            pytest.fail("core never came up on the socket")

        assert status == 200
        assert body["ok"] is True

        status, body = request_over_socket(sock_path, "/api/surface")
        assert status == 200
        # Nothing captured in an empty home, and the server says which kind of
        # empty that is rather than returning a bare list.
        assert body["state"] == "not_captured"
    finally:
        proc.terminate()
        proc.wait(timeout=15)

    # uvicorn's own cleanup, which is why `prepare_socket` only has to cope with
    # the killed case.
    assert not sock_path.exists()
