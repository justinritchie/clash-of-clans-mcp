"""Tests for the snapshot store."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from coc_mcp.snapshots import (
    list_snapshots,
    player_war_history,
    reconcile_with_warlog,
    snapshot_regular_war,
)


@pytest.fixture
def fixture_war():
    p = Path(__file__).parent / "fixtures" / "sample_regular_war.json"
    return json.loads(p.read_text())


@pytest.fixture
def tmp_snapshot_dir(tmp_path):
    d = tmp_path / "snapshots"
    (d / "wars").mkdir(parents=True)
    (d / "cwl").mkdir(parents=True)
    (d / "cwl_groups").mkdir(parents=True)
    return d


def test_snapshot_writes_file(fixture_war, tmp_snapshot_dir):
    result = snapshot_regular_war(fixture_war, snapshot_dir=tmp_snapshot_dir)
    assert result["snapshotted"] is True
    assert Path(result["path"]).exists()
    data = json.loads(Path(result["path"]).read_text())
    assert "_snapshot_metadata" in data
    assert data["state"] == "warEnded"


def test_snapshot_dedup(fixture_war, tmp_snapshot_dir):
    snapshot_regular_war(fixture_war, snapshot_dir=tmp_snapshot_dir)
    second = snapshot_regular_war(fixture_war, snapshot_dir=tmp_snapshot_dir)
    assert second["snapshotted"] is False
    assert "Already" in second["reason"]


def test_snapshot_refuses_in_progress_war(fixture_war, tmp_snapshot_dir):
    war = dict(fixture_war)
    war["state"] = "inWar"
    result = snapshot_regular_war(war, snapshot_dir=tmp_snapshot_dir)
    assert result["snapshotted"] is False
    assert "force" in result["reason"]


def test_snapshot_force_overrides_state(fixture_war, tmp_snapshot_dir):
    war = dict(fixture_war)
    war["state"] = "inWar"
    result = snapshot_regular_war(war, snapshot_dir=tmp_snapshot_dir, force=True)
    assert result["snapshotted"] is True


def test_list_snapshots(fixture_war, tmp_snapshot_dir):
    snapshot_regular_war(fixture_war, snapshot_dir=tmp_snapshot_dir)
    listing = list_snapshots(snapshot_dir=tmp_snapshot_dir)
    assert listing["regular_war_count"] == 1


def test_reconcile_finds_gaps(fixture_war, tmp_snapshot_dir):
    snapshot_regular_war(fixture_war, snapshot_dir=tmp_snapshot_dir)
    fake_warlog = {
        "items": [
            {  # this matches what we snapshotted (same opponent tag + same day, slight time drift OK)
                "endTime": "20260422T000005.000Z",  # 5s later than the snapshot
                "result": "win",
                "opponent": {"name": "Test Opponents", "tag": "#OPP"},
                "clan": {"stars": 11, "destructionPercentage": 78.5},
                "attacksPerMember": 2,
                "teamSize": 5,
            },
            {  # this is a gap
                "endTime": "20260420T000000.000Z",
                "result": "lose",
                "opponent": {"name": "MissedWar", "tag": "#MISS"},
                "clan": {"stars": 8, "destructionPercentage": 65.0},
                "attacksPerMember": 2,
                "teamSize": 5,
            },
        ]
    }
    recon = reconcile_with_warlog(fake_warlog, snapshot_dir=tmp_snapshot_dir)
    assert recon["warlog_total"] == 2
    assert recon["snapshotted_count"] == 1
    assert recon["gap_count"] == 1
    assert recon["gaps"][0]["opponent_name"] == "MissedWar"


def test_player_war_history(fixture_war, tmp_snapshot_dir):
    snapshot_regular_war(fixture_war, snapshot_dir=tmp_snapshot_dir)
    history = player_war_history("#P1", n=5, snapshot_dir=tmp_snapshot_dir, our_clan_tag="#YV9JRULU")
    assert history["wars_found"] == 1
    assert history["aggregate"]["total_attacks_used"] == 2
    assert history["aggregate"]["avg_stars_per_attack"] == 3.0
    war = history["wars"][0]
    assert war["attacks_used"] == 2
    assert war["attacks_missed"] == 0


def test_player_history_aggregates_attendance(fixture_war, tmp_snapshot_dir):
    snapshot_regular_war(fixture_war, snapshot_dir=tmp_snapshot_dir)
    # NoShow used 0 of 2 attacks.
    history = player_war_history("#P5", n=5, snapshot_dir=tmp_snapshot_dir, our_clan_tag="#YV9JRULU")
    assert history["aggregate"]["attendance_pct"] == 0.0
    assert history["aggregate"]["total_attacks_owed"] == 2
