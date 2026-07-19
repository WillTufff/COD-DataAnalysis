#!/usr/bin/env python3
"""Fetch the per-game event tarballs from the CWL archive.

The upstream repo is gone, so these come from Software Heritage. Every id below
names exact bytes, so a fetch either reproduces the files or fails.

Run with --verify-csvs to re-hash the CSVs in snapshots/cwl-archive against the
same revision, confirming both tiers came from one version of the repo.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

API = "https://archive.softwareheritage.org/api/1"

# github.com/Activision/cwl-data, branch master, visited 2023-01-29.
ORIGIN = "https://github.com/Activision/cwl-data"
SNAPSHOT = "c5ee2cd04d10971b39685fc55da4747d04a0ba04"
REVISION = "5b7eb907b63ab4a53ed7fd2987459f3bf28c9c21"
DATA_DIR = "6ba76d21e331e17ab2998196560e8fa8a7d49058"
STRUCTURED_DIR = "f8b2a9fbafd3dc00907c86c2b4fe713b13492ee0"

# Event slug -> (filename, git blob id, bytes). Slugs match cwl_archive.manifest.
TARBALLS: dict[str, tuple[str, str, int]] = {
    "2017-champs": (
        "structured-2017-08-13-champs.tar.gz",
        "0e0cec8e7a9f4b0a7598612d85aa59eca99a2a12",
        2891816,
    ),  # noqa: E501
    "2018-dallas": (
        "structured-2017-12-10-dallas.tar.gz",
        "7ffc647227700d5730e49d863cd3eb0e11fed795",
        2118713,
    ),  # noqa: E501
    "2018-neworleans": (
        "structured-2018-01-14-neworleans.tar.gz",
        "3f4561420e2f67d5b2a5920e0bd719fd7088e032",
        2272930,
    ),  # noqa: E501
    "2018-atlanta": (
        "structured-2018-03-11-atlanta.tar.gz",
        "b1ccc3874da0035521b642ea9f18bbb4d11970be",
        2203001,
    ),  # noqa: E501
    "2018-birmingham": (
        "structured-2018-04-01-birmingham.tar.gz",
        "a11e0cb9a636df7abe15e87d4c5f01bbdd6f1d51",
        1356818,
    ),  # noqa: E501
    "2018-proleague1": (
        "structured-2018-04-08-proleague1.tar.gz",
        "b161482a560b49779f840e8205d70e87786c67f1",
        4110855,
    ),  # noqa: E501
    "2018-relegation": (
        "structured-2018-04-19-relegation.tar.gz",
        "c4be9d636b32c33b732992722b8b7b66c8baa986",
        290841,
    ),  # noqa: E501
    "2018-seattle": (
        "structured-2018-04-22-seattle.tar.gz",
        "1babc402fd7088982eb6a9185381cf989876fdc8",
        2184302,
    ),  # noqa: E501
    "2018-anaheim": (
        "structured-2018-06-17-anaheim.tar.gz",
        "9ef26f3560d7d97396d6f4e9c72a81236875e809",
        2214451,
    ),  # noqa: E501
    "2018-proleague2": (
        "structured-2018-07-29-proleague2.tar.gz",
        "ccbcf04dd3bca1a4f6d29449f56caea1b5fe6c16",
        3885946,
    ),  # noqa: E501
    "2018-champs": (
        "structured-2018-08-19-champs.tar.gz",
        "b3e5b3fb984ac9b269b81345c01740392e791443",
        2372635,
    ),  # noqa: E501
    "2019-proleague-qual": (
        "structured-2019-01-20-proleague-qual.tar.gz",
        "559ab317edaa8ca88608d18fc737646e2769ecbc",
        209286,
    ),  # noqa: E501
    "2019-proleague": (
        "structured-2019-07-05-proleague.tar.gz",
        "caa778c7c3b6155c969c40a3e0039734e64a7553",
        148644,
    ),  # noqa: E501
}

# Events that have a CSV but no event tarball upstream.
NO_STRUCTURED_TIER: tuple[str, ...] = (
    "2019-fortworth",
    "2019-london",
    "2019-anaheim",
    "2019-proleague-finals",
    "2019-champs",
)

DEST = Path(__file__).resolve().parents[1] / "snapshots" / "cwl-structured"


def git_blob_id(payload: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(payload) + payload).hexdigest()


def fetch(blob_id: str) -> bytes:
    url = f"{API}/content/sha1_git:{blob_id}/raw/"
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read()


def fetch_tarballs() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    failures = 0
    for slug, (filename, blob_id, size) in TARBALLS.items():
        target = DEST / filename
        if target.exists() and git_blob_id(target.read_bytes()) == blob_id:
            print(f"  ok (cached)  {slug:22} {filename}")
            continue
        payload = fetch(blob_id)
        got = git_blob_id(payload)
        if got != blob_id:
            print(f"  HASH MISMATCH {slug}: expected {blob_id}, got {got}", file=sys.stderr)
            failures += 1
            continue
        if len(payload) != size:
            print(f"  SIZE MISMATCH {slug}: expected {size}, got {len(payload)}", file=sys.stderr)
            failures += 1
            continue
        target.write_bytes(payload)
        print(f"  fetched      {slug:22} {filename}  {len(payload) / 1048576:.2f} MB")
    for slug in NO_STRUCTURED_TIER:
        print(f"  absent       {slug:22} (no structured tier upstream)")
    return failures


def verify_csvs() -> int:
    """Re-hash the committed CSVs against the archived directory listing."""
    import json

    url = f"{API}/directory/{DATA_DIR}/"
    with urllib.request.urlopen(url, timeout=120) as resp:
        entries = json.load(resp)
    upstream = {e["name"]: e["target"] for e in entries if e["type"] == "file"}
    local_dir = DEST.parent / "cwl-archive"
    failures = 0
    checked = 0
    for path in sorted(local_dir.glob("*.csv")):
        want = upstream.get(path.name)
        if want is None:
            print(f"  NOT IN ARCHIVE {path.name}", file=sys.stderr)
            failures += 1
            continue
        got = git_blob_id(path.read_bytes())
        if got != want:
            print(f"  MISMATCH {path.name}: local {got}, archive {want}", file=sys.stderr)
            failures += 1
        else:
            checked += 1
    print(f"  {checked} CSVs match the archived revision, {failures} failed")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--verify-csvs",
        action="store_true",
        help="check the committed CSVs against the same archived revision, then exit",
    )
    args = ap.parse_args()

    print(f"origin   {ORIGIN}")
    print(f"snapshot {SNAPSHOT}")
    print(f"revision {REVISION}")
    print(f"data/    {DATA_DIR}")
    print(f"structured/ {STRUCTURED_DIR}\n")

    if args.verify_csvs:
        return 1 if verify_csvs() else 0
    return 1 if fetch_tarballs() else 0


if __name__ == "__main__":
    raise SystemExit(main())
