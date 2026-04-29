"""Tests for tenure parsing."""
from __future__ import annotations

import json

import pytest

from coc_mcp.tenure import (
    list_cached_tenure,
    parse_duration_to_days,
    parse_tenure_markdown,
    read_tenure,
    write_tenure,
)


SAMPLE_ANKIT176 = """
# ankit176
United States of America [ Broken Arrow ](https://example.com)
...
Player's role in Clan
Member
2 Months 30 Days
Elder
1 Day
Player's Clans in the selected period
[ Broken Arrow  - #YV9JRULU  Total 2 Months 30 Days  2 Months 30 Days per stay  ](https://example.com)
"""

SAMPLE_TORRES = """
# Torres
Player's role in Clan
Member
3 Months 1 Day
Player's Clans in the selected period
[ Broken Arrow  - #YV9JRULU  Total 3 Months 1 Day  3 Months 1 Day per stay  ](https://example.com)
"""

SAMPLE_LONG_TENURE = """
# OldTimer
Player's role in Clan
Member
6 Months 0 Days
Elder
1 Year 2 Months 15 Days
Player's Clans in the selected period
[ Broken Arrow  - #YV9JRULU  Total 1 Year 8 Months 15 Days  1 Year 8 Months 15 Days per stay  ](https://example.com)
"""


def test_parse_duration_simple():
    assert parse_duration_to_days("5 Days") == 5
    assert parse_duration_to_days("2 Months 30 Days") == 90
    assert parse_duration_to_days("1 Month 1 Day") == 31
    assert parse_duration_to_days("1 Year") == 365
    assert parse_duration_to_days("") == 0


def test_parse_ankit176():
    parsed = parse_tenure_markdown(SAMPLE_ANKIT176)
    assert parsed["current_role"] == "Elder"
    assert len(parsed["role_breakdown"]) == 2
    assert parsed["role_breakdown"][0]["role"] == "Member"
    assert parsed["role_breakdown"][0]["days"] == 90
    assert parsed["role_breakdown"][1]["role"] == "Elder"
    assert parsed["role_breakdown"][1]["days"] == 1
    assert parsed["total_days_in_current_clan"] == 90  # 2 mo 30 days


def test_parse_torres_single_role():
    parsed = parse_tenure_markdown(SAMPLE_TORRES)
    assert parsed["current_role"] == "Member"
    assert len(parsed["role_breakdown"]) == 1
    assert parsed["total_days_in_current_clan"] == 91  # 3 mo 1 day


def test_parse_long_tenure():
    parsed = parse_tenure_markdown(SAMPLE_LONG_TENURE)
    assert parsed["current_role"] == "Elder"
    assert parsed["total_days_in_current_clan"] == 365 + 8 * 30 + 15  # 1 yr 8 mo 15 days


def test_cache_roundtrip(tmp_path):
    parsed = parse_tenure_markdown(SAMPLE_ANKIT176)
    write_tenure("#CR20YL9Q", "ankit176", parsed, snapshot_dir=tmp_path)
    cached = read_tenure("#CR20YL9Q", snapshot_dir=tmp_path)
    assert cached is not None
    assert cached["name"] == "ankit176"
    assert cached["current_role"] == "Elder"
    assert cached["total_days_in_current_clan"] == 90


def test_cache_handles_missing(tmp_path):
    assert read_tenure("#NONEXISTENT", snapshot_dir=tmp_path) is None


def test_list_cached_tenure(tmp_path):
    write_tenure("#A1", "P1", parse_tenure_markdown(SAMPLE_ANKIT176), snapshot_dir=tmp_path)
    write_tenure("#A2", "P2", parse_tenure_markdown(SAMPLE_TORRES), snapshot_dir=tmp_path)
    listed = list_cached_tenure(snapshot_dir=tmp_path)
    assert len(listed) == 2
