"""The metric-diff harness, on keys and streams rather than on a database.

The harness is the instrument every later phase reports through, so what is
exercised here is the part that can be quietly wrong: keys that move when the
data did not, a threshold that hides a real change, a truncated list that reads
as a complete one.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from cdlhub_analytics.metricdiff import ATOL, RTOL, compare, snapshot

# ----------------------------------------------------------------------- keys


def test_a_key_component_containing_the_separator_stays_one_component() -> None:
    assert snapshot.key("finding", "OpTic/Texas", "trend") == "finding/OpTic%2FTexas/trend"
    assert snapshot.key("a", "100%") == "a/100%25"


def test_extending_a_built_key_does_not_escape_it_again() -> None:
    head = snapshot.key("metric.player", "metric_layer@2.3.0", "Scump")
    assert snapshot.child(head, "kd", "z") == head + "/kd/z"


def test_a_repeated_name_is_disambiguated_and_a_unique_one_is_not() -> None:
    labels = snapshot._disambiguated(
        [(1, "Scump", "Scump"), (2, "Crimsix", None), (3, "Scump", "Scump_(EU)")]
    )
    assert labels[2] == "Crimsix"
    assert labels[1] == "Scump#Scump"
    assert labels[3] == "Scump#Scump_(EU)"


# ------------------------------------------------------------------ flattening


def test_a_payload_flattens_to_one_entry_per_leaf() -> None:
    entries = dict(snapshot.flatten({"a": {"b": 1.5}, "c": True, "d": None}, "root"))
    assert entries == {"root/a/b": 1.5, "root/c": True, "root/d": None}


def test_a_list_of_identified_elements_is_keyed_by_identity_not_position() -> None:
    before = dict(
        snapshot.flatten({"rows": [{"key": "kills", "v": 1.0}, {"key": "deaths", "v": 2.0}]}, "art")
    )
    after = dict(
        snapshot.flatten({"rows": [{"key": "deaths", "v": 2.0}, {"key": "kills", "v": 1.0}]}, "art")
    )
    assert before == after
    assert "art/rows/key=kills/v" in before


def test_two_elements_alike_in_one_key_field_are_kept_apart_by_the_others() -> None:
    entries = dict(
        snapshot.flatten(
            {
                "rows": [
                    {"model": "player_rating", "artifact": "rating_persistence", "v": 1.0},
                    {"model": "player_rating", "artifact": "roster_forecast", "v": 2.0},
                ]
            },
            "art",
        )
    )
    assert entries["art/rows/model=player_rating+artifact=rating_persistence/v"] == 1.0
    assert entries["art/rows/model=player_rating+artifact=roster_forecast/v"] == 2.0


def test_a_list_of_bare_values_is_keyed_by_position_and_reports_its_length() -> None:
    entries = dict(snapshot.flatten({"lo_hi": [0.1, 0.9]}, "art"))
    assert entries == {"art/lo_hi/len": 2.0, "art/lo_hi/0": 0.1, "art/lo_hi/1": 0.9}


def test_a_list_that_grows_reports_its_new_length() -> None:
    assert dict(snapshot.flatten({"xs": [1.0]}, "a"))["a/xs/len"] == 1.0
    assert dict(snapshot.flatten({"xs": [1.0, 2.0]}, "a"))["a/xs/len"] == 2.0


# ------------------------------------------------------------------- threshold


def test_a_difference_at_the_float32_floor_is_noise() -> None:
    assert not compare.moved(0.564, 0.564000001)
    assert not compare.moved(0.0, ATOL / 2)


def test_a_difference_above_the_floor_is_a_move() -> None:
    assert compare.moved(0.564, 0.5641)
    assert compare.moved(-0.2216, -0.3)


def test_the_threshold_scales_with_the_value() -> None:
    assert not compare.moved(1e6, 1e6 + 0.4, rtol=RTOL, atol=ATOL)
    assert compare.moved(1e-6, 2e-6, rtol=RTOL, atol=ATOL)


# ----------------------------------------------------------------------- merge


def _merge(before: dict[str, Any], after: dict[str, Any]) -> compare.Report:
    return compare.merge(iter(sorted(before.items())), iter(sorted(after.items())))


def test_an_unchanged_surface_reports_no_moves() -> None:
    surface = {"a/x": 1.0, "a/y": 2.0, "b/z": "held"}
    report = _merge(surface, surface)
    assert report.families["a"].moved == 0
    assert report.families["a"].compared == 2
    assert report.n_flips == 0


def test_a_moved_number_is_counted_and_named_with_both_values() -> None:
    report = _merge({"rapm/Scump/coef": 0.12}, {"rapm/Scump/coef": 0.19})
    assert report.families["rapm"].moved == 1
    move = report.movers[0][2]
    assert move.key == "rapm/Scump/coef"
    assert move.old == 0.12
    assert move.new == 0.19


def test_a_flipped_verdict_is_a_flip_rather_than_a_move() -> None:
    report = _merge({"art/excludes_zero": True}, {"art/excludes_zero": False})
    assert report.families["art"].moved == 0
    assert report.families["art"].flipped == 1
    assert report.flips[0].old is True


def test_a_rewritten_headline_is_a_flip() -> None:
    report = _merge({"finding/x/headline": "led the league"}, {"finding/x/headline": "second"})
    assert report.n_flips == 1


def test_a_number_that_becomes_null_is_a_flip_not_a_move() -> None:
    report = _merge({"era/x/rating": 1.0}, {"era/x/rating": None})
    assert report.families["era"].flipped == 1
    assert report.families["era"].moved == 0


def test_new_and_dropped_keys_are_counted_separately_from_moves() -> None:
    report = _merge({"a/gone": 1.0, "a/kept": 1.0}, {"a/kept": 1.0, "a/new": 1.0})
    counts = report.families["a"]
    assert (counts.added, counts.removed, counts.moved, counts.compared) == (1, 1, 0, 1)
    assert report.added == ["a/new"]
    assert report.removed == ["a/gone"]


def test_the_report_keeps_the_largest_moves_and_says_how_many_it_left_out() -> None:
    before = {f"a/{i}": 1.0 for i in range(200)}
    after = {f"a/{i}": 1.0 + (i + 1) / 1000 for i in range(200)}
    report = compare.merge(iter(sorted(before.items())), iter(sorted(after.items())), top=5)
    payload = compare.payload(report, {}, {}, "before", "after")
    assert payload["totals"]["moved"] == 200
    assert len(payload["movers"]) == 5
    assert payload["movers_omitted"] == 195
    # Largest first, and the largest move really is the largest.
    assert payload["movers"][0]["key"] == "a/199"


def test_a_move_from_zero_is_kept_rather_than_dropped_for_having_no_ratio() -> None:
    report = _merge({"a/x": 0.0}, {"a/x": 0.5})
    assert report.families["a"].moved == 1
    assert report.movers[0][2].key == "a/x"
    # No ratio exists off zero, and Infinity is not a number jsonb accepts.
    assert report.movers[0][2].rel is None


def test_a_move_from_zero_outranks_an_ordinary_relative_move() -> None:
    report = compare.merge(
        iter(sorted({"a/zero": 0.0, "a/small": 1.0}.items())),
        iter(sorted({"a/zero": 0.5, "a/small": 1.5}.items())),
        top=1,
    )
    assert report.movers[0][2].key == "a/zero"


def test_the_stored_report_is_valid_json_for_a_jsonb_column() -> None:
    """The whole payload has to survive `json.dumps(allow_nan=False)`: a
    non-finite number reaches Postgres as a literal it refuses, at the end of a
    run that has already done all of its work."""
    report = _merge({"a/x": 0.0, "a/y": 1.0}, {"a/x": 0.5, "a/y": 2.0})
    payload = compare.payload(report, {}, {}, "before", "after")
    assert json.loads(json.dumps(payload, allow_nan=False))["totals"]["moved"] == 2


def test_a_non_finite_stored_number_is_carried_as_text_not_as_a_float() -> None:
    """Only the metric tables forbid Infinity by constraint. Left as a float it
    reaches jsonb as a literal Postgres refuses, at the end of a whole run."""
    assert snapshot._value(float("inf")) == "inf"
    assert snapshot._value(float("nan")) == "nan"
    assert snapshot._value(1.5) == 1.5


def test_a_coefficient_that_becomes_infinite_reads_as_a_flip() -> None:
    report = _merge({"rapm/x/se": 0.3}, {"rapm/x/se": snapshot._value(float("inf"))})
    assert report.families["rapm"].flipped == 1
    assert report.families["rapm"].moved == 0


# ------------------------------------------------------------- round-tripping


def _write(path: Path, entries: list[tuple[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"format": snapshot.FORMAT, "runs": []}) + "\n")
        for entry in sorted(entries):
            handle.write(json.dumps(entry) + "\n")


def test_a_written_snapshot_reads_back_in_the_same_order(tmp_path: Path) -> None:
    entries = [("b/2", 2.0), ("a/1", 1.0), ("c/3", "held")]
    path = tmp_path / "snap.ndjson.gz"
    _write(path, entries)
    assert snapshot.header_of(path)["format"] == snapshot.FORMAT
    assert list(snapshot.read(path)) == sorted(entries)


def test_sorting_spills_to_disk_and_still_comes_back_ordered() -> None:
    source = [(f"k/{i:04d}", float(i)) for i in reversed(range(500))]
    ordered = list(snapshot.sorted_entries(iter(source), chunk=50))
    assert ordered == sorted(source)


def test_sorting_without_spilling_gives_the_same_answer() -> None:
    source = [(f"k/{i:04d}", float(i)) for i in reversed(range(20))]
    assert list(snapshot.sorted_entries(iter(source), chunk=1000)) == sorted(source)


# ===== provenance is not a published number =====


def test_run_id_leaves_are_not_compared() -> None:
    """Every refit renumbers the runs a finding cites. Counting those as moves
    fills the report with noise in a run where nothing changed."""
    baseline = [
        ("finding/insights@1.3.0/player/Simp/outlier/1/detail/era_run_id", 162),
        ("finding/insights@1.3.0/player/Simp/outlier/1/detail/kd_z", 2.5),
    ]
    current = [
        ("finding/insights@1.3.0/player/Simp/outlier/1/detail/era_run_id", 179),
        ("finding/insights@1.3.0/player/Simp/outlier/1/detail/kd_z", 2.5),
    ]
    report = compare.merge(iter(baseline), iter(current))
    counts = report.families["finding"]
    assert (counts.moved, counts.added, counts.removed) == (0, 0, 0)
    assert counts.compared == 1  # kd_z only


def test_a_real_move_beside_a_run_id_is_still_reported() -> None:
    baseline = [
        ("finding/insights@1.3.0/player/Simp/outlier/1/detail/era_run_id", 162),
        ("finding/insights@1.3.0/player/Simp/outlier/1/detail/kd_z", 2.5),
    ]
    current = [
        ("finding/insights@1.3.0/player/Simp/outlier/1/detail/era_run_id", 179),
        ("finding/insights@1.3.0/player/Simp/outlier/1/detail/kd_z", 2.9),
    ]
    report = compare.merge(iter(baseline), iter(current))
    assert report.families["finding"].moved == 1


def test_provenance_recognises_the_leaf_not_the_path() -> None:
    assert compare.provenance("finding/x/detail/era_run_id")
    assert compare.provenance("artifact/y/run_id")
    assert not compare.provenance("artifact/y/run_id/brier")
    assert not compare.provenance("metric.player/kd_z")
