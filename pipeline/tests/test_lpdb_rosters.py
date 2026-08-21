"""How a placement's roster slot finds its player, and when it refuses to.

The slot names a Liquipedia page, and the database stores a handle. The two
part company whenever a player renames: LPDB writes `Scrappy`, this database
says `Scrap`, and a resume that resolved on the handle alone lost every ring
that player won.
"""

from cdlhub_pipeline.identity import Aliases
from cdlhub_pipeline.lpdb.load import CATALOG_BACKFILL, LpdbLoader


def _loader(
    players: dict[str, int],
    pages: dict[str, int],
    ambiguous: set[str] | None = None,
    aliases: Aliases | None = None,
) -> LpdbLoader:
    loader = LpdbLoader.__new__(LpdbLoader)
    loader.aliases = aliases or Aliases(players={}, teams={}, orgs={})
    loader._players = players
    loader._player_pages = pages
    loader._ambiguous_handles = ambiguous or set()
    loader.report = {"roster_ambiguous_handles": []}
    return loader


def test_a_renamed_player_resolves_through_the_page() -> None:
    loader = _loader({"scrap": 1}, {"scrappy": 1})
    assert loader.player_slot("Scrappy") == 1


def test_a_player_whose_bio_never_attached_resolves_through_the_handle() -> None:
    loader = _loader({"landxn": 2}, {})
    assert loader.player_slot("Landxn") == 2


def test_a_source_spelling_resolves_through_the_alias_map() -> None:
    aliases = Aliases(
        players={}, teams={}, orgs={}, players_by_source={"lpdb": {"LlamGod": "LlamaGod"}}
    )
    loader = _loader({"llamagod": 3}, {}, aliases=aliases)
    assert loader.player_slot("LlamGod") == 3


def test_a_handle_two_people_answer_to_is_refused_and_named() -> None:
    """Liquipedia separates people by letter case, so folding can merge two."""
    loader = _loader({"ace": 4}, {}, ambiguous={"ace"})
    assert loader.player_slot("Ace") is None
    assert loader.report["roster_ambiguous_handles"] == ["Ace"]


def test_an_unknown_slot_is_left_unresolved() -> None:
    """A name on a bracket is not evidence that a player row should exist."""
    loader = _loader({}, {})
    assert loader.player_slot("MaNiaC") is None


def test_the_backfill_names_the_playoffs_after_the_local_convention() -> None:
    """Six seasons of it are `CDL Championship` locally, and Cito matches names."""
    assert CATALOG_BACKFILL["Call_of_Duty_League/Season_7/Playoffs"] == "CDL Championship"
    assert CATALOG_BACKFILL["Call_of_Duty_World_League/2017/Pro_League/Stage_1"] is None
