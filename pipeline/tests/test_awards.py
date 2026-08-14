from cdlhub_pipeline.lpdb.load import award_kind


def test_the_same_selection_folds_across_seasons() -> None:
    # 2022 names the award without its season, every year after names it with one
    assert award_kind("CDL First All-Star Team") == "first_team"
    assert award_kind("CDL 2023 Team of The Year") == "first_team"
    assert award_kind("CDL 2020 SCUF Team of The Year") == "first_team"
    assert award_kind("CDL Second All-Star Team") == "second_team"
    assert award_kind("CDL 2026 Second All-Star Team") == "second_team"


def test_second_team_never_reads_as_first() -> None:
    assert award_kind("CDL 2024 Second All-Star Team") != "first_team"


def test_the_referent_awards() -> None:
    assert award_kind("Rookie of the Year") == "roty"
    assert award_kind("Regular Season MVP") == "rs_mvp"
    assert award_kind("MVP") == "event_mvp"
    assert award_kind("Tournament MVP") == "event_mvp"
    assert award_kind("FMVP") == "fmvp"
    assert award_kind("Captain's MVP") == "captains_mvp"


def test_mode_awards_are_their_own_kind() -> None:
    # role- and mode-conditional, so they score the role model and not the rating
    assert award_kind("Best Hardpoint Player") == "mode_best"
    assert award_kind("Best Control Player") == "mode_best"
    assert award_kind("Best SnD Player") == "mode_best"


def test_an_unrecognised_award_is_named_not_bucketed() -> None:
    assert award_kind("Best Clip") == "unmapped"
    assert award_kind("Play of the Day") == "unmapped"
    assert award_kind("Most Kills in a Match") == "unmapped"
    assert award_kind("Caster's MVP") == "unmapped"
