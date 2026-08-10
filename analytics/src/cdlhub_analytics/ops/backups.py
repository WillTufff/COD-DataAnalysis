"""Database dumps: what exists, taking one, and restoring one.

The sources can be rebuilt from the snapshots on disk without touching the
network, but `model_runs` and everything hanging off it cannot: every past
backtest, the rating versions kept as baselines, and the code_ref that produced
each one live only in Postgres. This is the one place that can put them back.

Dumps are written outside the repository, in pg_dump's custom format, by
whichever pg_dump matches the server: the local binary when there is one, else
the one inside the compose container. Restoring is the only operation in the
ops surface that writes to the database, and it takes a dump of what it is
about to replace before it starts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import REPO_ROOT

DEFAULT_DIRECTORY = Path(os.environ.get("CDLHUB_BACKUPS") or Path.home() / ".cdlhub" / "backups")
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
COMPOSE_SERVICE = "db"
SUFFIX = ".dump"
# Dumps of this database run ~20MB, so ten of them is a few hundred megabytes
# and covers every recent state worth going back to.
KEEP = 10
# A GUI app inherits no login shell, so docker is looked for where the usual
# installers put it as well as on the PATH.
DOCKER_CANDIDATES = (
    "/opt/homebrew/bin/docker",
    "/usr/local/bin/docker",
    str(Path.home() / ".orbstack" / "bin" / "docker"),
    "/Applications/Docker.app/Contents/Resources/bin/docker",
)
# The compose default. A DSN pointing anywhere else can only be reached by a
# local pg_dump, never by the container's.
COMPOSE_DSN_HOSTS = ("localhost", "127.0.0.1")


class BackupError(RuntimeError):
    """A dump or restore that could not be run, or that failed."""


def _docker() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    for candidate in DOCKER_CANDIDATES:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _is_compose_dsn(dsn: str) -> bool:
    return any(f"@{host}:" in dsn or f"@{host}/" in dsn for host in COMPOSE_DSN_HOSTS)


def _database(dsn: str) -> str:
    tail = dsn.rsplit("/", 1)[-1]
    return tail.split("?")[0] or "cdlhub"


def _user(dsn: str) -> str:
    if "://" not in dsn or "@" not in dsn:
        return "cdlhub"
    credentials = dsn.split("://", 1)[1].split("@", 1)[0]
    return credentials.split(":", 1)[0] or "cdlhub"


def method(dsn: str) -> dict[str, Any]:
    """How a dump would be taken, and why it cannot be if it cannot.

    A local pg_dump is preferred because it works against any DSN; the
    container's is the fallback, and only for the database the container is
    serving.
    """
    local = shutil.which("pg_dump")
    if local and shutil.which("pg_restore") and shutil.which("psql"):
        return {"method": "local", "available": True, "binary": local, "reason": None}
    docker = _docker()
    if docker is None:
        return {
            "method": None,
            "available": False,
            "binary": None,
            "reason": "no pg_dump on the PATH and docker was not found",
        }
    if not _is_compose_dsn(dsn):
        return {
            "method": None,
            "available": False,
            "binary": docker,
            "reason": (
                "no local pg_dump, and the database is not the compose one, "
                "so the container's pg_dump cannot reach it"
            ),
        }
    if not COMPOSE_FILE.is_file():
        return {
            "method": None,
            "available": False,
            "binary": docker,
            "reason": f"no compose file at {COMPOSE_FILE}",
        }
    return {"method": "docker", "available": True, "binary": docker, "reason": None}


def _argv(how: dict[str, Any], dsn: str, tool: str, arguments: list[str]) -> list[str]:
    """The command line for pg_dump or pg_restore, either way of running it."""
    if how["method"] == "local":
        return [tool, *arguments, dsn]
    docker = how["binary"]
    return [
        docker,
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "exec",
        "-T",
        COMPOSE_SERVICE,
        tool,
        "-U",
        _user(dsn),
        "-d",
        _database(dsn),
        *arguments,
    ]


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(label: str) -> str:
    kept = [character if character.isalnum() else "-" for character in label.lower()]
    return "".join(kept).strip("-")[:40]


def entries(directory: Path) -> list[dict[str, Any]]:
    """Every dump in the directory, newest first."""
    if not directory.is_dir():
        return []
    found: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for path in directory.glob(f"*{SUFFIX}"):
        stat = path.stat()
        created = datetime.fromtimestamp(stat.st_mtime, UTC)
        found.append(
            {
                "name": path.name,
                "bytes": stat.st_size,
                "created_at": _stamp(created),
                "age_hours": round((now - created).total_seconds() / 3600, 2),
            }
        )
    return sorted(found, key=lambda entry: str(entry["created_at"]), reverse=True)


def _prune(directory: Path, keep: int) -> list[str]:
    dropped = [entry["name"] for entry in entries(directory)[keep:]]
    for name in dropped:
        try:
            (directory / name).unlink()
        except OSError:
            continue
    return dropped


def report(dsn: str, directory: Path = DEFAULT_DIRECTORY, keep: int = KEEP) -> dict[str, Any]:
    found = entries(directory)
    return {
        "directory": str(directory),
        "keep": keep,
        "total_bytes": sum(int(entry["bytes"]) for entry in found),
        "backups": found,
        **method(dsn),
    }


def create(
    dsn: str,
    directory: Path = DEFAULT_DIRECTORY,
    label: str | None = None,
    keep: int = KEEP,
) -> dict[str, Any]:
    """Take a dump, prune the oldest past `keep`, and report what is there now.

    The dump is written to a temporary name and moved into place, so an
    interrupted run leaves no half-file that looks restorable.
    """
    how = method(dsn)
    if not how["available"]:
        raise BackupError(str(how["reason"]))

    directory.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    suffix = f"-{_slug(label)}" if label and _slug(label) else ""
    name = f"cdlhub-{started.strftime('%Y%m%dT%H%M%SZ')}{suffix}{SUFFIX}"
    final = directory / name
    partial = directory / f"{name}.partial"

    argv = _argv(how, dsn, "pg_dump", ["--format=custom", "--no-owner"])
    try:
        with partial.open("wb") as handle:
            completed = subprocess.run(  # noqa: S603
                argv, stdout=handle, stderr=subprocess.PIPE, check=False
            )
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise BackupError(f"could not run {argv[0]}: {exc}") from exc

    if completed.returncode != 0 or partial.stat().st_size == 0:
        detail = completed.stderr.decode(errors="replace").strip() or "no output"
        partial.unlink(missing_ok=True)
        raise BackupError(f"pg_dump failed: {detail}")

    partial.replace(final)
    result = report(dsn, directory, keep)
    result["created"] = name
    result["duration_s"] = round((datetime.now(UTC) - started).total_seconds(), 1)
    result["pruned"] = _prune(directory, keep)
    result["backups"] = entries(directory)
    return result


def _empty_schema(how: dict[str, Any], dsn: str) -> None:
    """Drop everything in the public schema and recreate it empty.

    pg_restore --clean only drops what the dump contains, which leaves any
    table, view or type created after the dump was taken standing while the
    schema_migrations rows that explain it are replaced. Emptying the schema
    first makes the restore a replacement rather than a merge.
    """
    argv = _argv(
        how,
        dsn,
        "psql",
        ["-v", "ON_ERROR_STOP=1", "-q", "-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"],
    )
    try:
        completed = subprocess.run(argv, capture_output=True, check=False)  # noqa: S603
    except OSError as exc:
        raise BackupError(f"could not run {argv[0]}: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip() or "no output"
        raise BackupError(f"could not empty the schema: {detail}")


def restore(
    dsn: str,
    name: str,
    directory: Path = DEFAULT_DIRECTORY,
    keep: int = KEEP,
) -> dict[str, Any]:
    """Replace the database with a dump, after dumping what is there now.

    The schema is emptied first, so what is left afterwards is the dump and
    nothing else. Restoring into an empty schema should produce no errors at
    all, which is why this one asks pg_restore to stop at the first.
    """
    how = method(dsn)
    if not how["available"]:
        raise BackupError(str(how["reason"]))

    source = directory / name
    if source.name != name or not source.is_file():
        raise BackupError(f"no backup named {name} in {directory}")

    safety = create(dsn, directory, label="pre-restore", keep=keep)
    started = datetime.now(UTC)
    _empty_schema(how, dsn)
    argv = _argv(how, dsn, "pg_restore", ["--no-owner", "--exit-on-error"])
    try:
        with source.open("rb") as handle:
            completed = subprocess.run(  # noqa: S603
                argv, stdin=handle, capture_output=True, check=False
            )
    except OSError as exc:
        raise BackupError(f"could not run {argv[0]}: {exc}") from exc

    messages = completed.stderr.decode(errors="replace").strip()
    if completed.returncode != 0:
        raise BackupError(
            f"pg_restore failed: {messages or 'no output'} — the database is now empty; "
            f"the state it replaced is in {safety.get('created')}"
        )

    result = report(dsn, directory, keep)
    result["restored"] = name
    result["safety_backup"] = safety.get("created")
    result["duration_s"] = round((datetime.now(UTC) - started).total_seconds(), 1)
    result["messages"] = messages.splitlines()[-20:]
    return result
