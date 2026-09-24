"""A payload stamped under a team's old slug still reconciles with its fixture."""

from cdlhub_pipeline.cito.transform import _restamp

NAMES = {
    "la-guerrillas-m8": "Los Angeles Guerrillas M8",
    "paris-gentle-mates": "Los Angeles Guerrillas M8",
    "toronto-ultra": "Toronto Ultra",
    "boston-breach": "Boston Breach",
}


def _payload(*slugs: str) -> dict[str, list[dict[str, str]]]:
    return {"players": [{"playerName": f"p{i}", "teamSlug": s} for i, s in enumerate(slugs)]}


def test_an_old_slug_for_a_series_team_takes_the_series_slug() -> None:
    out = _restamp(
        _payload("paris-gentle-mates", "toronto-ultra"),
        {"Los Angeles Guerrillas M8": "la-guerrillas-m8", "Toronto Ultra": "toronto-ultra"},
        lambda slug: NAMES.get(slug, slug),
    )
    assert [p["teamSlug"] for p in out["players"]] == ["la-guerrillas-m8", "toronto-ultra"]


def test_a_slug_for_some_other_team_is_left_for_the_quarantine() -> None:
    data = _payload("boston-breach", "toronto-ultra")
    out = _restamp(
        data,
        {"Los Angeles Guerrillas M8": "la-guerrillas-m8", "Toronto Ultra": "toronto-ultra"},
        lambda slug: NAMES.get(slug, slug),
    )
    assert out is data


def test_the_segment_breakdown_is_relabelled_with_the_players() -> None:
    data = {
        "players": [
            {
                "playerName": "p0",
                "teamSlug": "paris-gentle-mates",
                "maps": [{"breakdown": {"teamGameStats": [{"teamSlug": "paris-gentle-mates"}]}}],
            }
        ]
    }
    out = _restamp(
        data,
        {"Los Angeles Guerrillas M8": "la-guerrillas-m8", "Toronto Ultra": "toronto-ultra"},
        lambda slug: NAMES.get(slug, slug),
    )
    player = out["players"][0]
    assert player["teamSlug"] == "la-guerrillas-m8"
    assert player["maps"][0]["breakdown"]["teamGameStats"][0]["teamSlug"] == "la-guerrillas-m8"
