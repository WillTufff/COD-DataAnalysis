from cdlhub_pipeline import venue


def _rules(**events: dict[str, object]) -> venue.VenueRules:
    return venue.VenueRules(events=dict(events))


def test_lpdb_type_decides_when_nothing_is_curated() -> None:
    rules = _rules()
    assert venue.derive(rules, 2024, "CDL Major 1", "Offline").is_lan is True
    assert venue.derive(rules, 2024, "CDL Major 1 Qualifiers", "Online").is_lan is False


def test_a_mixed_event_is_undecided_rather_than_guessed() -> None:
    verdict = venue.derive(_rules(), 2026, "CDL Regular Season", "Online/Offline")
    assert verdict.is_lan is None
    assert verdict.source == venue.SOURCE_UNDECIDED
    assert "Online/Offline" in verdict.reason


def test_an_event_with_no_tournament_page_is_undecided() -> None:
    verdict = venue.derive(_rules(), 2026, "CDL Launch Invitational", None)
    assert verdict.is_lan is None
    assert verdict.source == venue.SOURCE_UNDECIDED


def test_a_curated_verdict_beats_the_source() -> None:
    rules = _rules(
        **{"2018:CWL Seattle 2018": {"is_lan": True, "reviewed": False, "reason": "open LAN"}}
    )
    verdict = venue.derive(rules, 2018, "CWL Seattle 2018", "Online")
    assert verdict.is_lan is True
    assert verdict.source == venue.SOURCE_CURATED
    assert verdict.reviewed is False


def test_curated_entries_are_keyed_by_season_as_well_as_name() -> None:
    rules = _rules(**{"2018:CWL Anaheim 2018": {"is_lan": True, "reviewed": True, "reason": "x"}})
    assert venue.derive(rules, 2019, "CWL Anaheim 2018", None).source == venue.SOURCE_UNDECIDED


def test_the_shipped_rules_load_and_every_entry_states_a_reason() -> None:
    rules = venue.VenueRules.load()
    assert rules.events
    for key, entry in rules.events.items():
        assert isinstance(entry.get("is_lan"), bool), key
        assert entry.get("reason"), key
        assert "reviewed" in entry, key
