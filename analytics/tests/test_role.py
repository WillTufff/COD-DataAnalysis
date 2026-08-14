"""Role: the weapon table, the recovery test, and the entry cost."""

from __future__ import annotations

import numpy as np
import pytest

from cdlhub_analytics import role


def _contact(
    player_id: int, season_id: int, contact: float, kd: float, maps: int = 40
) -> role.ContactSeason:
    return role.ContactSeason(
        player_id=player_id,
        season_id=season_id,
        maps=maps,
        contact_rate=contact,
        contact_win_rate=0.5,
        kd=kd,
        damage_per_map=500.0 + kd,
        untraded_rate=0.5,
    )


# ------------------------------------------------------------ the weapon table


def test_every_weapon_carries_one_of_the_declared_classes() -> None:
    assert set(role.WEAPON_CLASS.values()) <= {
        role.SMG,
        role.AR,
        role.SNIPER,
        role.TACTICAL,
        role.OTHER,
    }


def test_the_table_agrees_with_every_class_the_kill_feed_observes() -> None:
    observed = {name: role.WEAPON_CLASS[name] for name in role.VERIFIED}
    assert role.table_disagreements(observed) == []


def test_a_table_entry_contradicting_the_feed_is_reported() -> None:
    """The plan this phase came from called the KBAR-32 an SMG; the feed says ar."""
    observed = {"KBAR-32": role.SMG}
    assert any("KBAR-32" in line for line in role.table_disagreements(observed))


def test_every_verified_weapon_is_in_the_table() -> None:
    assert set(role.WEAPON_CLASS) >= role.VERIFIED


# --------------------------------------------------------- the recovery rule


def test_the_recovery_rule_reads_its_three_bands() -> None:
    assert role.verdict_for(0.80) == "the axes carry role"
    assert role.verdict_for(0.70) == "ambiguous between loose axes and a loose proxy"
    assert role.verdict_for(0.50) == "the axes do not carry role"


def test_the_recovery_rule_is_inclusive_at_its_boundaries() -> None:
    assert role.verdict_for(role.RECOVERY_CARRIES) == "the axes carry role"
    assert role.verdict_for(role.RECOVERY_AMBIGUOUS) != "the axes do not carry role"


def test_auc_is_one_when_a_column_separates_perfectly() -> None:
    scores = np.asarray([1.0, 2.0, 3.0, 4.0])
    labels = np.asarray([False, False, True, True], dtype=bool)
    assert role._auc(scores, labels) == pytest.approx(1.0)


def test_auc_is_a_half_when_a_column_says_nothing() -> None:
    scores = np.asarray([1.0, 2.0, 3.0, 4.0])
    labels = np.asarray([True, False, False, True], dtype=bool)
    assert role._auc(scores, labels) == pytest.approx(0.5)


def test_recovery_finds_a_class_that_the_axes_separate() -> None:
    seasons, axes = [], {}
    for i in range(40):
        smg = i % 2 == 0
        seasons.append(
            role.WeaponSeason(
                player_id=i, season_id=1, label=role.SMG if smg else role.AR, maps=30, dominance=1.0
            )
        )
        axes[(i, 1)] = np.asarray([3.0 if smg else -3.0, 0.0], dtype=float)
    rec = role.recovery(seasons, axes)
    assert rec is not None
    assert rec.accuracy == pytest.approx(1.0)
    assert rec.verdict == "the axes carry role"


def test_recovery_lands_near_the_base_rate_when_the_axes_say_nothing() -> None:
    rng = np.random.default_rng(0)
    seasons, axes = [], {}
    for i in range(60):
        seasons.append(
            role.WeaponSeason(
                player_id=i,
                season_id=1,
                label=role.SMG if i % 2 else role.AR,
                maps=30,
                dominance=1.0,
            )
        )
        axes[(i, 1)] = rng.normal(size=2)
    rec = role.recovery(seasons, axes)
    assert rec is not None
    assert rec.accuracy < 0.75


def test_recovery_holds_out_the_player_rather_than_the_season() -> None:
    """One player's seasons in the training set would score memory, not recovery."""
    seasons, axes = [], {}
    for player in range(20):
        for season in (1, 2):
            seasons.append(
                role.WeaponSeason(
                    player_id=player,
                    season_id=season,
                    label=role.SMG if player % 2 else role.AR,
                    maps=30,
                    dominance=1.0,
                )
            )
            axes[(player, season)] = np.asarray([float(player % 2), 0.0])
    rec = role.recovery(seasons, axes)
    assert rec is not None
    assert rec.n_players == 20
    assert rec.n_seasons == 40


def test_recovery_declines_when_no_season_carries_a_style_position() -> None:
    seasons = [role.WeaponSeason(1, 1, role.SMG, 30, 1.0)]
    assert role.recovery(seasons, {}) is None


# ------------------------------------------------------------ the entry cost


def test_a_season_is_standardised_inside_its_own_season() -> None:
    out = role._standardise_within_season([1.0, 2.0, 3.0, 10.0, 20.0, 30.0], [1, 1, 1, 2, 2, 2])
    assert out[:3] == pytest.approx(out[3:])
    assert float(np.mean(out[:3])) == pytest.approx(0.0, abs=1e-12)


def test_a_season_with_no_spread_standardises_to_zero() -> None:
    assert role._standardise_within_season([5.0, 5.0], [1, 1]) == pytest.approx([0.0, 0.0])


def test_the_slope_recovers_a_planted_relationship() -> None:
    seasons = [_contact(i, 1, contact=float(i), kd=2.0 * i) for i in range(1, 31)]
    cost = role.entry_cost(seasons, "kd")
    assert cost is not None
    assert cost.slope == pytest.approx(1.0, abs=1e-9)


def test_the_slope_is_flat_when_contact_says_nothing_about_the_outcome() -> None:
    seasons = [_contact(i, 1, contact=float(i), kd=1.0) for i in range(1, 31)]
    cost = role.entry_cost(seasons, "kd")
    assert cost is not None
    assert cost.slope == pytest.approx(0.0)


def test_the_interval_resamples_players_not_seasons() -> None:
    seasons = [_contact(i // 2, 1 + i % 2, contact=float(i), kd=float(i)) for i in range(20)]
    cost = role.entry_cost(seasons, "kd")
    assert cost is not None
    assert cost.n_players == 10
    assert cost.n_seasons == 20


def test_renumbering_the_players_moves_neither_the_slope_nor_the_interval() -> None:
    """The resample seeds from contents, so surrogate keys cannot permute it."""
    base = [_contact(i, 1, contact=float(i), kd=float(i % 7)) for i in range(1, 41)]
    shifted = [
        _contact(s.player_id + 9000, 1, contact=s.contact_rate, kd=s.kd) for s in reversed(base)
    ]
    first, second = role.entry_cost(base, "kd"), role.entry_cost(shifted, "kd")
    assert first is not None and second is not None
    assert first.slope == pytest.approx(second.slope)
    assert (first.lo95, first.hi95) == pytest.approx((second.lo95, second.hi95))


def test_a_population_too_small_to_fit_returns_nothing() -> None:
    assert role.entry_cost([_contact(1, 1, 1.0, 1.0)], "kd") is None


# ------------------------------------------------------ raw, adjusted, and gap


def test_the_adjustment_and_the_adjusted_value_sum_back_to_the_raw_one() -> None:
    seasons = [_contact(i, 1, contact=float(i), kd=2.0 * i) for i in range(1, 21)]
    cost = role.entry_cost(seasons, "kd")
    rows = role.adjusted_kd(seasons, cost)
    assert len(rows) == len(seasons)
    for row in rows:
        assert row["raw"] + row["adjustment"] == pytest.approx(row["adjusted"], abs=1e-9)


def test_no_adjustment_ships_without_a_cost_behind_it() -> None:
    assert role.adjusted_kd([_contact(1, 1, 1.0, 1.0)], None) == []
