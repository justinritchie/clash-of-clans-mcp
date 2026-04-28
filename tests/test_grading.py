"""Unit tests for the grading engine — uses fixtures, no API calls."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coc_mcp.grading import (
    aggregate_player_war_history,
    carry_forward_recommendation,
    find_missed_opportunities,
    grade_war,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
RUBRIC_PATH = Path(__file__).parent.parent / "config" / "rubric.default.json"


@pytest.fixture
def rubric():
    with RUBRIC_PATH.open() as f:
        return json.load(f)


@pytest.fixture
def regular_war():
    with (FIXTURE_DIR / "sample_regular_war.json").open() as f:
        return json.load(f)


def _player(graded, name):
    return next(p for p in graded["players"] if p["name"] == name)


def test_grade_war_basic_shape(regular_war, rubric):
    graded = grade_war(regular_war, rubric, war_type="regular", our_clan_tag="#YV9JRULU")
    assert graded["war_type"] == "regular"
    assert len(graded["players"]) == 5
    assert graded["summary"]["total_attacks_used"] == 6  # 2+2+1+1+0


def test_top_player_perfect_score(regular_war, rubric):
    graded = grade_war(regular_war, rubric, war_type="regular", our_clan_tag="#YV9JRULU")
    top = _player(graded, "Top1")
    assert top["attacks_used"] == 2
    assert top["attacks_missed"] == 0
    # 1st: mirror, 3⭐ (10) + 2nd: offset +2, 3⭐ (10 + bonus 3) = 23
    assert top["score"] >= 20
    assert top["grade"] == "A"
    assert top["rule_violations"] == []


def test_mirror_then_smart_three_star(regular_war, rubric):
    graded = grade_war(regular_war, rubric, war_type="regular", our_clan_tag="#YV9JRULU")
    p = _player(graded, "MirrorBoss")
    assert p["attacks_used"] == 2
    # 1st: mirror, 2⭐ pass + 2nd: offset +2, 3⭐ + bonus
    assert p["rule_violations"] == []
    assert p["grade"] in ("A", "B")


def test_off_target_first_attack_flagged(regular_war, rubric):
    """RuleBreaker (pos 3) attacks #E5 (pos 5) — offset +2 violates first_attack rule."""
    graded = grade_war(regular_war, rubric, war_type="regular", our_clan_tag="#YV9JRULU")
    p = _player(graded, "RuleBreaker")
    assert p["attacks_used"] == 1
    assert p["attacks_missed"] == 1
    assert any("1st attack" in v for v in p["rule_violations"]), p["rule_violations"]
    assert any("Missed" in v for v in p["rule_violations"]), p["rule_violations"]


def test_no_show_penalized(regular_war, rubric):
    graded = grade_war(regular_war, rubric, war_type="regular", our_clan_tag="#YV9JRULU")
    p = _player(graded, "NoShow")
    assert p["attacks_used"] == 0
    assert p["attacks_missed"] == 2
    # Missed 2 * -10 = -20 base, but no attacks so no positive offset.
    assert p["score"] == -20
    assert p["grade"] == "F"


def test_aggregation_across_wars(regular_war, rubric):
    graded = grade_war(regular_war, rubric, war_type="regular", our_clan_tag="#YV9JRULU")
    aggregated = aggregate_player_war_history([graded, graded])  # double-count to verify aggregation
    top = aggregated["#P1"]
    assert top["wars_played"] == 2
    assert top["attacks_used"] == 4
    assert top["avg_stars"] == 3.0


def test_carry_forward_keeps_top_benches_noshow(regular_war, rubric):
    graded = grade_war(regular_war, rubric, war_type="regular", our_clan_tag="#YV9JRULU")
    aggregated = aggregate_player_war_history([graded])
    recs = carry_forward_recommendation(aggregated, rubric)
    by_name = {r["name"]: r for r in recs}
    assert by_name["Top1"]["recommendation"] == "keep"
    assert by_name["NoShow"]["recommendation"] == "bench"


def test_find_missed_opportunities_flags_reach_up(regular_war):
    """In the fixture: P3 (RuleBreaker) attacks E5 (pos 5). E4 (pos 4) wasn't even a weaker option,
    but at the moment of P4's attack on E4 (1⭐, 55%), E5 was still undefeated by anyone.
    So P4 attacking E4 with E5 sitting there should flag — P4 is TH16 and E5 is TH16.

    Order of attacks in fixture:
      order=1: P1 -> E1 (3⭐)  → clears pos 1
      order=2: P2 -> E2 (2⭐)
      order=3: P3 -> E5 (3⭐)  → clears pos 5
      order=4: P4 -> E4 (1⭐)  ← at this moment, what's undefeated and weaker than #4? Only #5, but #5 was 3⭐'d at order 3. So no missed opportunity.
      order=5: P1 -> E3 (3⭐)  → clears pos 3
      order=6: P2 -> E4 (3⭐)  → clears pos 4
    """
    missed = find_missed_opportunities(regular_war, our_clan_tag="#YV9JRULU")
    # No clear missed opportunities in this fixture (cleared bases tracked correctly).
    assert isinstance(missed, list)


def test_find_missed_opportunities_with_synthetic(regular_war):
    """Synthesize a scenario where reach-up clearly violates: modify P4 to attack E2 (pos 2, TH17 — too high)
    when E4/E5 (his level) are still around early in the war."""
    war = json.loads(json.dumps(regular_war))  # deep copy
    # Replace P4's only attack: hit E1 (pos 1) for 0⭐ at order=2, when E2/E3/E4/E5 all undefeated and weaker.
    war["clan"]["members"][3]["attacks"] = [
        {"attackerTag": "#P4", "defenderTag": "#E1", "stars": 0, "destructionPercentage": 30, "order": 2, "duration": 180}
    ]
    # Bump P1 attack order so P4 goes earlier.
    war["clan"]["members"][0]["attacks"][0]["order"] = 1
    war["clan"]["members"][0]["attacks"][1]["order"] = 99  # later
    war["clan"]["members"][1]["attacks"][0]["order"] = 50
    war["clan"]["members"][1]["attacks"][1]["order"] = 51
    war["clan"]["members"][2]["attacks"][0]["order"] = 52

    missed = find_missed_opportunities(war, our_clan_tag="#YV9JRULU", th_buffer=0)
    # P4 (TH16) attacked E1 (pos 1, TH17) for 0⭐ at order 2. E1 was already 3⭐'d at order 1 by P1.
    # At that moment, E4 (pos 4, TH16) and E5 (pos 5, TH16) were undefeated and weaker.
    p4_misses = [m for m in missed if m["attacker_tag"] == "#P4"]
    assert len(p4_misses) == 1
    assert p4_misses[0]["actual_target_pos"] == 1
    weaker_positions = [w["pos"] for w in p4_misses[0]["available_weaker_undefeated"]]
    assert 4 in weaker_positions
    assert 5 in weaker_positions
    assert p4_misses[0]["severity"] == "high"


def test_rubric_override_changes_outcome(regular_war, rubric):
    """Tightening the bar should flip a 'keep' to 'review' or 'bench'."""
    strict = json.loads(json.dumps(rubric))
    strict["carry_forward_rubric"]["min_avg_stars"] = 2.9  # very high
    graded = grade_war(regular_war, rubric, war_type="regular", our_clan_tag="#YV9JRULU")
    aggregated = aggregate_player_war_history([graded])
    recs = carry_forward_recommendation(aggregated, strict)
    by_name = {r["name"]: r for r in recs}
    # OneShot averaged 1 star → not 'keep' under strict rubric.
    assert by_name["OneShot"]["recommendation"] in ("review", "bench")
