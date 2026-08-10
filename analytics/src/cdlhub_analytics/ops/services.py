"""Whether the local development stack is up: the web dev server and Postgres.

Neither is part of the pipeline; both have to be running before anything else
here is useful, and each is reported the way it is actually observed — a
listening socket and a process for the dev server, a connection for Postgres —
rather than from a pid file that outlives what wrote it.

Every probe is best effort and short: a tool that is missing, slow or refuses to
answer leaves its fields null instead of failing the command.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, cast

import psycopg

from . import REPO_ROOT
from .schema import dsn_host

WEB_DIR = REPO_ROOT / "web"
DEFAULT_WEB_PORT = 3000
PROBE_TIMEOUT_S = 3.0
TOOL_TIMEOUT_S = 4.0


def _run(argv: list[str]) -> str | None:
    """stdout of a short-lived tool, or None if it is missing or fails."""
    if shutil.which(argv[0]) is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            argv, capture_output=True, text=True, timeout=TOOL_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _listener_pids(port: int) -> list[int]:
    out = _run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"])
    if not out:
        return []
    return sorted({int(line) for line in out.split() if line.isdigit()})


def _process(pid: int) -> dict[str, Any]:
    """Command, elapsed time and start time for a pid, as ps reports them."""
    out = _run(["ps", "-o", "etime=,lstart=,command=", "-p", str(pid)])
    if not out or not out.strip():
        return {"pid": pid}
    fields = out.strip().split(maxsplit=6)
    if len(fields) < 7:
        return {"pid": pid, "command": out.strip()}
    return {
        "pid": pid,
        "uptime": fields[0],
        "started_at": " ".join(fields[1:6]),
        "command": fields[6],
    }


def _probe(port: int) -> dict[str, Any]:
    """One GET at the root: the page a browser would land on."""
    started = time.monotonic()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/", method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_S) as response:  # noqa: S310
            status = int(response.status)
            response.read(1024)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"responded": False, "error": str(exc.reason if hasattr(exc, "reason") else exc)}
    return {
        "responded": True,
        "status": status,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def web(port: int = DEFAULT_WEB_PORT) -> dict[str, Any]:
    """The Next.js dev server: a listener, the process behind it, and a page.

    A socket that accepts while the root times out is a server still compiling,
    which is the state worth telling apart from down.
    """
    listening = _listening(port)
    pids = _listener_pids(port) if listening else []
    probe = _probe(port) if listening else None
    state = "down"
    if listening:
        state = "up" if probe and probe.get("responded") else "starting"
    return {
        "name": "web",
        "label": "Next.js dev server",
        "state": state,
        "port": port,
        "url": f"http://localhost:{port}",
        "directory": str(WEB_DIR),
        "argv": ["npm", "run", "dev"],
        "processes": [_process(pid) for pid in pids],
        "probe": probe,
    }


def _container(port: int) -> dict[str, Any] | None:
    """The compose container publishing the database port, if docker answers."""
    out = _run(["docker", "ps", "--all", "--no-trunc", "--format", "{{json .}}"])
    if not out:
        return None
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if f":{port}->" not in str(row.get("Ports") or ""):
            continue
        return {
            "name": row.get("Names"),
            "image": row.get("Image"),
            "state": row.get("State"),
            "status": row.get("Status"),
        }
    return None


def database(dsn: str) -> dict[str, Any]:
    host = dsn_host(dsn)
    port = int(host.rsplit(":", 1)[-1]) if host.rsplit(":", 1)[-1].isdigit() else 5432
    payload: dict[str, Any] = {
        "name": "database",
        "label": "Postgres",
        "port": port,
        "dsn_host": host,
        "directory": str(REPO_ROOT),
        "argv": ["docker", "compose", "up", "-d", "db"],
    }
    try:
        with psycopg.connect(dsn, connect_timeout=2) as conn:
            row = conn.execute(
                "SELECT pg_database_size(current_database()), version(), "
                "(SELECT count(*) FROM pg_stat_activity WHERE datname = current_database())"
            ).fetchone()
        payload["state"] = "up"
        if row is not None:
            payload["size_bytes"] = cast(int, row[0])
            payload["version"] = str(row[1]).split(" on ")[0]
            payload["connections"] = cast(int, row[2])
    except psycopg.Error as exc:
        payload["state"] = "down"
        payload["error"] = str(exc).strip()
    payload["container"] = _container(port)
    return payload


def report(dsn: str, web_port: int = DEFAULT_WEB_PORT) -> dict[str, Any]:
    return {"services": [web(web_port), database(dsn)]}
