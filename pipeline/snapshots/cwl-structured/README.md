# CWL structured event feeds (2017–2018)

Per-map event feeds — the kill feed and round scores — for the 2017 and 2018 Call of
Duty World League seasons. Thirteen gzip tarballs, one per tournament, read in place by
the importer (no extraction needed). Each contains one JSON per game with `events`
(death, spawn, roundstart, roundend), player rosters, and map/mode metadata.

These come from the **same** `Activision/cwl-data` repository as the box scores in
`../cwl-archive/`, under the same **BSD 3-Clause** licence (see `../cwl-archive/LICENSE`).
The 2019 Black Ops 4 games in that repository carry empty event lists, so only 2017–2018
is present here.

## Provenance and reproducibility

The upstream repository was taken down, so this data is recovered from Software Heritage
and pinned so the fetch is reproducible:

- origin:   `https://github.com/Activision/cwl-data`
- snapshot: `c5ee2cd04d10971b39685fc55da4747d04a0ba04`
- revision: `5b7eb907b63ab4a53ed7fd2987459f3bf28c9c21`

`pipeline/scripts/fetch_structured.py` re-fetches these tarballs from that revision and,
with `--verify-csvs`, re-hashes the box-score CSVs in `../cwl-archive/` against the same
revision — confirming both tiers came from one version of the source.
