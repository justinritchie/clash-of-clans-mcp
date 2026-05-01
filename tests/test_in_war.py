"""Tests for in-war status helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from coc_mcp.in_war import in_war_status, in_war_status_markdown


@pytest.fixture
def in_war_fixture():
    """Synthesize a mid-war scenario from the regular war fixture."""
    base = json.loads((Path(__file__).parent / "fixtures" / "sample_regular_war.json").read_text())
    base["state"] = "inWar"
    # End the war 6 hours from a fixed "now" reference.
    base["endTime"] = "20260422T120000.000Z"
    base["startTime"] = "20260421T120000.000Z"
    # Strip P5 (NoShow)'s lack of attacks so we have one player who hasn't gone yet —
    # plus P3 (RuleBreaker) only used 1 of 2 attacks. So 1 missed + 1 partial pending.
    return base


@pytest.fixture
def now_during_war():
    return datetime(2026, 4, 22, 6, 0, 0, tzinfo=timezone.utc)  # 6 hours before end


def test_in_war_status_basic(in_war_fixture, now_during_war):
    status = in_war_status(in_war_fixture, our_clan_tag="#YV9JRULU", now=now_during_war)
    assert status["state"] == "inWar"
    assert status["is_active"] is True
    assert status["time_remaining_seconds"] == 6 * 3600
    assert "until war ends" in status["time_remaining_pretty"]


def test_in_war_status_score(in_war_fixture, now_during_war):
    status = in_war_status(in_war_fixture, our_clan_tag="#YV9JRULU", now=now_during_war)
    score = status["score"]
    assert score["us"]["name"] == "Broken Arrow"
    assert score["us"]["stars"] == 11
    assert score["them"]["stars"] == 9
    assert score["gap_stars"] == 2
    assert score["leading"] == "us"


def test_in_war_status_pending_attackers(in_war_fixture, now_during_war):
    status = in_war_status(in_war_fixture, our_clan_tag="#YV9JRULU", now=now_during_war)
    pending = {p["name"]: p for p in status["pending_attackers"]}
    # P3 (RuleBreaker) used 1/2; P4 (OneShot) used 1/2; P5 (NoShow) used 0/2.
    assert "RuleBreaker" in pending
    assert pending["RuleBreaker"]["attacks_owed"] == 1
    assert "NoShow" in pending
    assert pending["NoShow"]["attacks_owed"] == 2
    # P1 (Top1), P2 (MirrorBoss) used both — completed.
    completed = {p["name"] for p in status["completed_attackers"]}
    assert "Top1" in completed
    assert "MirrorBoss" in completed


def test_in_war_status_projection(in_war_fixture, now_during_war):
    status = in_war_status(in_war_fixture, our_clan_tag="#YV9JRULU", now=now_during_war)
    proj = status["projection"]
    assert proj["pending_attacks"] == 4  # 1 (RuleBreaker) + 1 (OneShot) + 2 (NoShow)
    assert proj["avg_stars_per_attack_so_far"] > 0
    assert proj["expected_final_stars"] > status["score"]["us"]["stars"]


def test_in_war_status_markdown_output(in_war_fixture, now_during_war):
    status = in_war_status(in_war_fixture, our_clan_tag="#YV9JRULU", now=now_during_war)
    md = in_war_status_markdown(status)
    assert "Battle Day" in md
    assert "Broken Arrow" in md
    assert "Pending attacks" in md
    assert "NoShow" in md
    assert "Projection" in md


def test_not_in_war():
    status = in_war_status({"state": "notInWar"})
    assert status["is_active"] is False
    md = in_war_status_markdown(status)
    assert "not currently in a war" in md.lower()
