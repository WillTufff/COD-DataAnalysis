from cdlhub_pipeline.cito.transform import CONTROL_ROUND, HILL, SND_ROUND, parse_segments

PAIR = {"boston-breach", "los-angeles-thieves"}


def _breakdown() -> dict[str, object]:
    return {
        "teamGameStats": [
            {
                "teamSlug": "boston-breach",
                "hardpoint": {"hillScores": [{"hill": 1, "score": 33}, {"hill": 2, "score": 52}]},
                "control": {"rounds": [{"round": 1, "won": True, "winType": "ticks"}]},
                "searchAndDestroy": {"rounds": []},
            },
            {
                "teamSlug": "los-angeles-thieves",
                "hardpoint": {"hillScores": []},
                "control": {"rounds": [{"round": 1, "won": False, "winType": "ticks"}]},
                "searchAndDestroy": {"rounds": [{"round": 1, "won": True, "winType": None}]},
            },
            # A slug that is not one of the series' two teams, as the stray
            # stat lines in the same payloads are.
            {
                "teamSlug": "toronto-koi",
                "hardpoint": {"hillScores": [{"hill": 1, "score": 99}]},
                "control": {"rounds": []},
                "searchAndDestroy": {"rounds": []},
            },
        ]
    }


def test_parse_segments_reads_every_mode_block() -> None:
    segments = parse_segments(_breakdown(), PAIR)
    hills = [s for s in segments if s.kind == HILL]
    assert [(s.ordinal, s.score) for s in hills] == [(1, 33), (2, 52)]
    assert all(s.team_slug == "boston-breach" for s in hills)

    control = sorted((s for s in segments if s.kind == CONTROL_ROUND), key=lambda s: s.team_slug)
    assert [(s.team_slug, s.won, s.win_type) for s in control] == [
        ("boston-breach", True, "ticks"),
        ("los-angeles-thieves", False, "ticks"),
    ]

    snd = [s for s in segments if s.kind == SND_ROUND]
    assert [(s.ordinal, s.won, s.win_type) for s in snd] == [(1, True, None)]


def test_parse_segments_drops_teams_outside_the_series() -> None:
    segments = parse_segments(_breakdown(), PAIR)
    assert {s.team_slug for s in segments} == PAIR


def test_parse_segments_skips_entries_with_no_index() -> None:
    breakdown = {
        "teamGameStats": [
            {
                "teamSlug": "boston-breach",
                "hardpoint": {"hillScores": [{"hill": None, "score": 33}, {"hill": 3}]},
                "control": {"rounds": [{"round": None, "won": True}]},
            }
        ]
    }
    assert parse_segments(breakdown, PAIR) == []


def test_parse_segments_tolerates_an_empty_breakdown() -> None:
    assert parse_segments({}, PAIR) == []
    assert parse_segments({"teamGameStats": []}, PAIR) == []
