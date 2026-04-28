"""Unit tests for the grading engine — uses fixtures, no API calls."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coc_mcp.grading import (
    aggregate_player_war_history,
    carry_forward_recommendation,
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
