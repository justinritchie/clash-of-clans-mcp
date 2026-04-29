#!/usr/bin/env python3
"""Clash of Clans MCP Server.

Wraps the official Clash of Clans REST API and adds workflow tools for
clan leadership: war grading, performance reports, carry-forward
recommendations, and elder-promotion screening.

Docs: https://developer.clashofclans.com/#/documentation
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from mcp.server.fastmcp import FastMCP

from coc_mcp.client import CocApiError, CocClient
from coc_mcp.config import (
    get_api_token,
    get_default_clan_tag,
    load_rubric,
)
from coc_mcp.grading import (
    aggregate_player_war_history,
    carry_forward_recommendation,
    find_missed_opportunities,
    grade_war,
    promotion_candidates,
)
from coc_mcp.reporting import carry_forward_markdown, missed_opportunities_markdown, war_report_markdown
from coc_mcp.snapshots import (
    list_snapshots,
    player_war_history,
    reconcile_with_warlog,
    snapshot_cwl_war as _snapshot_cwl_war,
    snapshot_regular_war,
)
from coc_mcp.tenure import list_cached_tenure, read_tenure, update_api_role


mcp = FastMCP("coc_mcp")


class ResponseFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"


# --- Helpers ---------------------------------------------------------------


def _client() -> CocClient:
    return CocClient(token=get_api_token())


def _resolve_clan_tag(tag: Optional[str]) -> str:
    if tag:
        return tag
    default = get_default_clan_tag()
    if not default:
        raise ValueError(
            "No clan tag provided and COC_DEFAULT_CLAN_TAG is not set. "
            "Pass clan_tag explicitly or set the env var."
        )
    return default


def _err(e: Exception) -> str:
    if isinstance(e, CocApiError):
        return f"Error ({e.status}): {e}"
    return f"Error: {type(e).__name__}: {e}"


# --- Input Models ----------------------------------------------------------


class GetClanInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    clan_tag: Optional[str] = Field(
        default=None,
        description="Clan tag like '#YV9JRULU' (with or without #). Falls back to COC_DEFAULT_CLAN_TAG env var.",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)


class GetWarlogInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    clan_tag: Optional[str] = Field(default=None)
    limit: int = Field(default=10, ge=1, le=50, description="Max number of past wars to list (1-50).")
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)


class GetPlayerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    player_tag: str = Field(..., min_length=2, description="Player tag like '#CR20YL9Q'.")
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)


class GetCwlWarInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    war_tag: str = Field(..., min_length=2, description="Individual CWL war tag (from leaguegroup.rounds).")
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)


class WarType(str, Enum):
    REGULAR = "regular"
    CWL = "cwl"


class GradeWarInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    clan_tag: Optional[str] = Field(default=None, description="Clan tag (default from env).")
    war_type: WarType = Field(
        default=WarType.REGULAR,
        description="'regular' = current regular war (2 attacks/player); 'cwl' = current CWL war (must also pass war_tag for non-current rounds).",
    )
    war_tag: Optional[str] = Field(
        default=None,
        description="Individual CWL war tag if grading a specific CWL round. Ignored for regular wars.",
    )
    rubric_overrides: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Per-call overrides for the rubric. Deep-merged onto the loaded rubric. Keys: war_attack_rubric, cwl_attack_rubric, etc.",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)


class WarReportInput(GradeWarInput):
    """Same input as GradeWar but always returns markdown leadership report."""
    pass


class CarryForwardInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    clan_tag: Optional[str] = Field(default=None)
    rubric_overrides: Optional[Dict[str, Any]] = Field(default=None)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class PromotionCandidatesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    clan_tag: Optional[str] = Field(default=None)
    include_war_history: bool = Field(
        default=True,
        description="If true, also pulls current CWL war history to factor into the score. If false, scores on donations + tenure only (faster).",
    )
    rubric_overrides: Optional[Dict[str, Any]] = Field(default=None)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


def _deep_merge(base: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Deep-merge overrides onto base. Returns a new dict."""
    if not overrides:
        return base
    out = json.loads(json.dumps(base))  # cheap deep copy
    def merge(a: Dict[str, Any], b: Dict[str, Any]) -> None:
        for k, v in b.items():
            if k in a and isinstance(a[k], dict) and isinstance(v, dict):
                merge(a[k], v)
            else:
                a[k] = v
    merge(out, overrides)
    return out


def _format(payload: Any, fmt: ResponseFormat) -> str:
    if fmt == ResponseFormat.MARKDOWN and isinstance(payload, str):
        return payload
    return json.dumps(payload, indent=2, default=str)


# --- Read-only API tools ---------------------------------------------------


@mcp.tool(
    name="clash_get_clan",
    annotations={"title": "Get clan details", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_get_clan(params: GetClanInput) -> str:
    """Get clan-level details (name, level, members count, war league, war wins/losses).

    Args:
        params: GetClanInput with optional clan_tag.

    Returns:
        JSON string with the full clan object from the COC API.
    """
    try:
        tag = _resolve_clan_tag(params.clan_tag)
        data = await _client().get_clan(tag)
        return _format(data, params.response_format)
    except Exception as e:
        return _err(e)


@mcp.tool(
    name="clash_get_clan_members",
    annotations={"title": "List clan members", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_get_clan_members(params: GetClanInput) -> str:
    """List current clan members with role, donations, war stars, trophies, etc.

    Args:
        params: GetClanInput with optional clan_tag.

    Returns:
        JSON or markdown table of members.
    """
    try:
        tag = _resolve_clan_tag(params.clan_tag)
        data = await _client().get_clan_members(tag)
        if params.response_format == ResponseFormat.MARKDOWN:
            members = data.get("items", [])
            lines = [f"# Members ({len(members)})", ""]
            lines.append("| Rank | Name | Tag | Role | TH | Trophies | Donated | Received |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for m in members:
                lines.append(
                    f"| {m.get('clanRank', '?')} | {m['name']} | {m['tag']} | "
                    f"{m.get('role', '?')} | {m.get('townHallLevel', '?')} | "
                    f"{m.get('trophies', '?')} | {m.get('donations', 0)} | "
                    f"{m.get('donationsReceived', 0)} |"
                )
            return "\n".join(lines)
        return _format(data, params.response_format)
    except Exception as e:
        return _err(e)


@mcp.tool(
    name="clash_get_warlog",
    annotations={"title": "Get clan war log", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_get_warlog(params: GetWarlogInput) -> str:
    """Get the clan's war log (past wars, summary only — no per-attack data).

    Note: per-attack details for past wars are NOT in this endpoint. Only summary
    (result, stars, destruction). For full attack data, snapshot wars at warEnded
    state via clash_get_current_war.

    Args:
        params: GetWarlogInput with limit (1-50).
    """
    try:
        tag = _resolve_clan_tag(params.clan_tag)
        data = await _client().get_warlog(tag, limit=params.limit)
        return _format(data, params.response_format)
    except Exception as e:
        return _err(e)


@mcp.tool(
    name="clash_get_current_war",
    annotations={"title": "Get current regular war", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_get_current_war(params: GetClanInput) -> str:
    """Get the current regular clan war with full attack data.

    State will be one of: notInWar, preparation, inWar, warEnded.
    If the clan has war log set to private, returns 403.
    """
    try:
        tag = _resolve_clan_tag(params.clan_tag)
        data = await _client().get_current_war(tag)
        return _format(data, params.response_format)
    except Exception as e:
        return _err(e)


@mcp.tool(
    name="clash_get_cwl_group",
    annotations={"title": "Get current CWL group", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_get_cwl_group(params: GetClanInput) -> str:
    """Get the current Clan War League group (8 clans, 7 rounds, war tags per round)."""
    try:
        tag = _resolve_clan_tag(params.clan_tag)
        data = await _client().get_cwl_group(tag)
        return _format(data, params.response_format)
    except Exception as e:
        return _err(e)


@mcp.tool(
    name="clash_get_cwl_war",
    annotations={"title": "Get specific CWL war", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_get_cwl_war(params: GetCwlWarInput) -> str:
    """Get an individual CWL war by warTag (from cwl_group.rounds[].warTags[])."""
    try:
        data = await _client().get_cwl_war(params.war_tag)
        return _format(data, params.response_format)
    except Exception as e:
        return _err(e)


@mcp.tool(
    name="clash_get_player",
    annotations={"title": "Get player details", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_get_player(params: GetPlayerInput) -> str:
    """Get a player's full profile: hero levels, troops, war stars, donations, current clan."""
    try:
        data = await _client().get_player(params.player_tag)
        return _format(data, params.response_format)
    except Exception as e:
        return _err(e)


class RawGetInput(BaseModel):
    """Generic GET passthrough for COC API endpoints not wrapped by a dedicated tool."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    path: str = Field(
        ...,
        min_length=2,
        description=(
            "API path, e.g. '/leagues', '/locations/32000006/rankings/clans', "
            "'/clans/%23YV9JRULU/capitalraidseasons', '/goldpass/seasons/current'. "
            "Tags must be URL-encoded ('#' -> '%23'). Full docs at "
            "https://developer.clashofclans.com/#/documentation"
        ),
    )
    query_params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional query string parameters as a dict, e.g. {'limit': 20, 'after': 'cursor'}.",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)


@mcp.tool(
    name="clash_api_get",
    annotations={"title": "Generic COC API GET passthrough", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_api_get(params: RawGetInput) -> str:
    """Hit any COC API GET endpoint that doesn't have a dedicated tool yet.

    Use the dedicated tools (clash_get_clan, clash_get_clan_members, clash_get_warlog,
    clash_get_current_war, clash_get_cwl_group, clash_get_cwl_war, clash_get_player)
    when applicable — they're better-typed.

    Use THIS tool for things like:
      - /leagues, /leagues/{id}, /leagues/{id}/seasons, /leagues/{id}/seasons/{seasonId}/rankings
      - /warleagues, /warleagues/{id}
      - /capitalleagues, /capitalleagues/{id}
      - /builderbaseleagues, /builderbaseleagues/{id}
      - /locations, /locations/{id}, /locations/{id}/rankings/{type}
      - /clans (search) — query_params: {'name': 'Broken Arrow', 'minMembers': 30, ...}
      - /clans/{tag}/capitalraidseasons
      - /clans/{tag}/labels
      - /goldpass/seasons/current
      - /labels/clans, /labels/players

    Path examples:
      /leagues
      /clans/%23YV9JRULU/capitalraidseasons
      /locations/32000006/rankings/clans
      /clans?name=Broken+Arrow&minMembers=30

    Refer to https://developer.clashofclans.com/#/documentation for the full endpoint catalog.
    """
    try:
        data = await _client().raw_get(params.path, params=params.query_params)
        return _format(data, params.response_format)
    except Exception as e:
        return _err(e)


# --- Workflow tools --------------------------------------------------------


@mcp.tool(
    name="clash_grade_war",
    annotations={"title": "Grade war attacks against rubric", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_grade_war(params: GradeWarInput) -> str:
    """Apply the configurable rubric to a war's attacks. Returns per-player grades.

    For regular wars (war_type='regular'), uses the war_attack_rubric (mirror+1, smart 2nd).
    For CWL wars (war_type='cwl'), uses cwl_attack_rubric. Pass war_tag for a specific CWL round.

    Returns:
        JSON with players[].score/grade/rule_violations, and a summary block.
    """
    try:
        rubric = _deep_merge(load_rubric(), params.rubric_overrides)
        client = _client()
        if params.war_type == WarType.CWL and params.war_tag:
            war = await client.get_cwl_war(params.war_tag)
            our_clan_tag = None  # auto-detect by side
            if params.clan_tag:
                our_clan_tag = params.clan_tag
        else:
            tag = _resolve_clan_tag(params.clan_tag)
            war = await client.get_current_war(tag)
            our_clan_tag = war.get("clan", {}).get("tag")

        if war.get("state") in (None, "notInWar"):
            return "No active war to grade. State: notInWar."

        graded = grade_war(war, rubric, war_type=params.war_type.value, our_clan_tag=our_clan_tag)
        return _format(graded, params.response_format)
    except Exception as e:
        return _err(e)


@mcp.tool(
    name="clash_war_report",
    annotations={"title": "Leadership war post-mortem", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_war_report(params: WarReportInput) -> str:
    """Generate a markdown post-mortem of the war answering 4 leadership questions:
    (1) who used both attacks, (2) who followed the rules, (3) performance leaderboard,
    (4) smart-attack honor roll.

    Use this when you want a quick human-readable readout. For raw data, use clash_grade_war.
    """
    try:
        rubric = _deep_merge(load_rubric(), params.rubric_overrides)
        client = _client()
        if params.war_type == WarType.CWL and params.war_tag:
            war = await client.get_cwl_war(params.war_tag)
            our_clan_tag = params.clan_tag
        else:
            tag = _resolve_clan_tag(params.clan_tag)
            war = await client.get_current_war(tag)
            our_clan_tag = war.get("clan", {}).get("tag")

        if war.get("state") in (None, "notInWar"):
            return "No active war to report on. State: notInWar."

        graded = grade_war(war, rubric, war_type=params.war_type.value, our_clan_tag=our_clan_tag)
        missed = find_missed_opportunities(war, our_clan_tag=our_clan_tag)
        meta = {
            "opponent_name": war.get("opponent", {}).get("name", "Opponent"),
            "result": _war_result(war, our_clan_tag),
        }
        return war_report_markdown(graded, war_meta=meta, missed=missed)
    except Exception as e:
        return _err(e)


class MissedOpportunitiesInput(BaseModel):
    """Input for the standalone missed-opportunities analysis."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    clan_tag: Optional[str] = Field(default=None, description="Clan tag (default from env).")
    war_type: WarType = Field(default=WarType.REGULAR, description="'regular' or 'cwl'.")
    war_tag: Optional[str] = Field(default=None, description="CWL war tag if grading a specific CWL round.")
    th_buffer: int = Field(
        default=0,
        ge=0,
        le=3,
        description="Allow flagged 'weaker' targets up to this many TH levels above the attacker (default 0 = strictly ≤ attacker TH).",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="clash_missed_opportunities",
    annotations={"title": "Find smart-attack misses (lower base was undefeated)", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_missed_opportunities(params: MissedOpportunitiesInput) -> str:
    """For each attack in the war, check if a weaker undefeated base was sitting there.

    Walks attacks in chronological order, tracks which opponent bases have been
    3-starred at each step, and flags any attack that:
      1. Did NOT 3-star, AND
      2. Was made when a weaker (higher mapPosition) opponent at TH ≤ attacker_th + th_buffer
         was still undefeated.

    This is the deeper version of the smart-3⭐ rule: not just "did they reach up too far"
    but "given what was actually available at the time, could they have 3-starred a weaker
    base instead?"

    Returns markdown by default with high/medium/low severity buckets.
    """
    try:
        client = _client()
        if params.war_type == WarType.CWL and params.war_tag:
            war = await client.get_cwl_war(params.war_tag)
            our_clan_tag = params.clan_tag
        else:
            tag = _resolve_clan_tag(params.clan_tag)
            war = await client.get_current_war(tag)
            our_clan_tag = war.get("clan", {}).get("tag")

        if war.get("state") in (None, "notInWar"):
            return "No active war to analyze. State: notInWar."

        missed = find_missed_opportunities(war, our_clan_tag=our_clan_tag, th_buffer=params.th_buffer)
        if params.response_format == ResponseFormat.MARKDOWN:
            return missed_opportunities_markdown(missed)
        return _format(missed, params.response_format)
    except Exception as e:
        return _err(e)


def _war_result(war: Dict[str, Any], our_tag: Optional[str]) -> str:
    state = war.get("state", "")
    if state != "warEnded":
        return state
    our = war.get("clan", {})
    them = war.get("opponent", {})
    if our_tag and them.get("tag") == our_tag:
        our, them = them, our
    if our.get("stars", 0) > them.get("stars", 0):
        return "Victory"
    if our.get("stars", 0) < them.get("stars", 0):
        return "Defeat"
    o, t = our.get("destructionPercentage", 0), them.get("destructionPercentage", 0)
    if o > t:
        return "Victory (tiebreak)"
    if o < t:
        return "Defeat (tiebreak)"
    return "Draw"


@mcp.tool(
    name="clash_carry_forward_recommendation",
    annotations={"title": "Recommend keep/bench for next CWL", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_carry_forward_recommendation(params: CarryForwardInput) -> str:
    """Pull current CWL group + each round, grade everyone, and produce a keep/review/bench list.

    Uses carry_forward_rubric. May make 8+ API calls (1 group + 7 round wars).
    """
    try:
        rubric = _deep_merge(load_rubric(), params.rubric_overrides)
        client = _client()
        tag = _resolve_clan_tag(params.clan_tag)

        group = await client.get_cwl_group(tag)
        if "rounds" not in group:
            return "No CWL group available — clan is not currently in CWL."

        graded_wars: List[Dict[str, Any]] = []
        for round_obj in group["rounds"]:
            for war_tag in round_obj.get("warTags", []):
                if war_tag in (None, "#0"):
                    continue
                try:
                    war = await client.get_cwl_war(war_tag)
                except CocApiError:
                    continue
                # Only include wars where our clan participated.
                if war.get("clan", {}).get("tag") == tag or war.get("opponent", {}).get("tag") == tag:
                    graded_wars.append(grade_war(war, rubric, war_type="cwl", our_clan_tag=tag))

        if not graded_wars:
            return "No CWL wars found for this clan in the current group."

        aggregated = aggregate_player_war_history(graded_wars)
        recs = carry_forward_recommendation(aggregated, rubric)
        if params.response_format == ResponseFormat.MARKDOWN:
            return carry_forward_markdown(recs)
        return _format(recs, params.response_format)
    except Exception as e:
        return _err(e)


@mcp.tool(
    name="clash_promotion_candidates",
    annotations={"title": "Screen members for elder promotion", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_promotion_candidates(params: PromotionCandidatesInput) -> str:
    """Apply the elder_promotion_rubric to current Members (excludes existing elders/co-leaders/leader).

    If include_war_history=True, also factors in current CWL performance.
    """
    try:
        rubric = _deep_merge(load_rubric(), params.rubric_overrides)
        client = _client()
        tag = _resolve_clan_tag(params.clan_tag)
        members_data = await client.get_clan_members(tag)
        members = members_data.get("items", [])

        aggregated: Dict[str, Dict[str, Any]] = {}
        if params.include_war_history:
            try:
                group = await client.get_cwl_group(tag)
                graded_wars: List[Dict[str, Any]] = []
                for round_obj in group.get("rounds", []):
                    for war_tag in round_obj.get("warTags", []):
                        if war_tag in (None, "#0"):
                            continue
                        try:
                            war = await client.get_cwl_war(war_tag)
                        except CocApiError:
                            continue
                        if war.get("clan", {}).get("tag") == tag or war.get("opponent", {}).get("tag") == tag:
                            graded_wars.append(grade_war(war, rubric, war_type="cwl", our_clan_tag=tag))
                aggregated = aggregate_player_war_history(graded_wars)
            except CocApiError:
                pass  # not in CWL — fall through with empty aggregated

        # Load tenure cache if available — enriches the score.
        tenure_lookup: Dict[str, Dict[str, Any]] = {}
        for entry in list_cached_tenure():
            tenure_lookup[entry["tag"]] = entry

        candidates = promotion_candidates(members, aggregated, rubric, tenure_lookup=tenure_lookup)

        if params.response_format == ResponseFormat.MARKDOWN:
            eligible = [c for c in candidates if c["eligible"]]
            ineligible = [c for c in candidates if not c["eligible"]]
            lines = ["# Elder Promotion Candidates", ""]
            lines.append(f"**Eligible**: {len(eligible)} · **Below bar**: {len(ineligible)}")
            lines.append("")
            for label, group_list in (("✅ Eligible", eligible), ("⚠️ Below bar", ineligible)):
                lines.append(f"## {label} ({len(group_list)})")
                lines.append("")
                if not group_list:
                    lines.append("_(none)_")
                    lines.append("")
                    continue
                lines.append("| Name | TH | Tenure | Donated | Received | Ratio | War Score | Reasons |")
                lines.append("|---|---|---|---|---|---|---|---|")
                for c in group_list:
                    tenure = f"{c.get('tenure_days')} d" if c.get('tenure_days') is not None else "—"
                    lines.append(
                        f"| {c['name']} | {c['th']} | {tenure} | {c['donations']} | {c['received']} | "
                        f"{c['donation_ratio']} | {c['war_score_window']} | {'; '.join(c['reasons'])} |"
                    )
                lines.append("")
            return "\n".join(lines)
        return _format(candidates, params.response_format)
    except Exception as e:
        return _err(e)


# --- Snapshot store tools -------------------------------------------------


class SnapshotInput(BaseModel):
    """Input for clash_snapshot_war. All fields optional — designed for scheduled-task invocation."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    clan_tag: Optional[str] = Field(default=None, description="Clan tag (default from env).")
    force: bool = Field(
        default=False,
        description="If True, snapshot even an in-progress war. Default False = only snapshot warEnded states.",
    )
    include_cwl: bool = Field(
        default=True,
        description="If True, also snapshot any warEnded CWL round wars from the current league group.",
    )


@mcp.tool(
    name="clash_snapshot_war",
    annotations={"title": "Save current war(s) to local snapshot store", "readOnlyHint": False, "openWorldHint": True},
)
async def clash_snapshot_war(params: SnapshotInput) -> str:
    """Snapshot the current regular war (and optionally current CWL round wars) to local disk.

    Idempotent — safe to call repeatedly. Dedupes by endTime + opponent.
    Designed to be invoked by a scheduled Claude task every ~2 days.

    Returns a JSON summary of what was snapshotted (or skipped, with reasons).
    """
    try:
        client = _client()
        tag = _resolve_clan_tag(params.clan_tag)
        results: Dict[str, Any] = {"regular": None, "cwl": []}

        # Regular war.
        try:
            war = await client.get_current_war(tag)
            results["regular"] = snapshot_regular_war(war, force=params.force)
        except CocApiError as e:
            results["regular"] = {"snapshotted": False, "reason": f"API error: {e}", "path": None}

        # CWL group + each warEnded round.
        if params.include_cwl:
            try:
                group = await client.get_cwl_group(tag)
                season = group.get("season", "unknown")
                for round_obj in group.get("rounds", []):
                    for war_tag in round_obj.get("warTags", []):
                        if war_tag in (None, "#0"):
                            continue
                        try:
                            cwl_war = await client.get_cwl_war(war_tag)
                            if cwl_war.get("state") == "warEnded":
                                # Only snapshot if our clan was in this war.
                                if cwl_war.get("clan", {}).get("tag") == tag or cwl_war.get("opponent", {}).get("tag") == tag:
                                    res = _snapshot_cwl_war(cwl_war, season=season, war_tag=war_tag)
                                    results["cwl"].append({"war_tag": war_tag, **res})
                        except CocApiError:
                            continue
            except CocApiError:
                results["cwl"] = {"skipped": "Not currently in CWL."}

        return _format(results, ResponseFormat.JSON)
    except Exception as e:
        return _err(e)


@mcp.tool(
    name="clash_snapshot_status",
    annotations={"title": "Show snapshot store contents + reconciliation gaps", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_snapshot_status(params: GetClanInput) -> str:
    """Report what's in the snapshot store and identify gaps vs the warlog.

    Useful as the second step in a scheduled task — first snapshot, then check
    if there are gaps to flag (gaps are unrecoverable but worth knowing about).
    """
    try:
        listing = list_snapshots()
        # Try to compare to warlog.
        try:
            client = _client()
            tag = _resolve_clan_tag(params.clan_tag)
            warlog = await client.get_warlog(tag, limit=20)
            recon = reconcile_with_warlog(warlog)
        except Exception as e:
            recon = {"error": f"Could not pull warlog for reconciliation: {e}"}

        return _format({"listing": listing, "reconciliation": recon}, ResponseFormat.JSON)
    except Exception as e:
        return _err(e)


class PlayerHistoryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    player_tag: str = Field(..., min_length=2, description="Player tag like '#PLCVY2G2Q'.")
    n: int = Field(default=10, ge=1, le=50, description="Max number of recent wars to include.")
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)


@mcp.tool(
    name="clash_player_war_history",
    annotations={"title": "Player's per-attack history from snapshot store", "readOnlyHint": True, "openWorldHint": True},
)
async def clash_player_war_history(params: PlayerHistoryInput) -> str:
    """Pull a player's attacks across recent stored war snapshots.

    Returns a flat timeline of attacks (target position, stars, destruction)
    plus aggregate stats (attendance, avg stars/destruction). Only works for
    wars that have been snapshotted — historical wars without snapshots are
    invisible to this tool.

    Use clash_snapshot_status to see how many snapshots exist.
    """
    try:
        clan = get_default_clan_tag()
        # Normalize clan tag for matching.
        from coc_mcp.client import normalize_tag
        clan_norm = normalize_tag(clan) if clan else None
        history = player_war_history(params.player_tag, n=params.n, our_clan_tag=clan_norm)
        return _format(history, params.response_format)
    except Exception as e:
        return _err(e)


# --- Tenure tools ----------------------------------------------------------


class TenureInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    player_tag: str = Field(..., min_length=2, description="Player tag like '#CR20YL9Q'.")


@mcp.tool(
    name="clash_get_tenure",
    annotations={"title": "Read cached tenure (days in clan) for a player", "readOnlyHint": True, "openWorldHint": False},
)
async def clash_get_tenure(params: TenureInput) -> str:
    """Read cached tenure for a player from the local cache.

    The official COC API doesn't expose tenure (days in clan). This tool reads
    from a local cache populated by scraping clashofstats.com via Claude (chat
    or scheduled task using crawl4ai). Returns a clear "not cached" message if
    the player hasn't been scraped yet.

    Cache lives at: snapshots/tenure/{tag}.json
    """
    try:
        cached = read_tenure(params.player_tag)
        if cached is None:
            return _format(
                {
                    "tag": params.player_tag,
                    "cached": False,
                    "message": (
                        "No tenure data cached for this player. To populate: in chat, "
                        "ask Claude to refresh tenure for this player by scraping "
                        f"https://www.clashofstats.com/players/{params.player_tag.lstrip('#')}/summary "
                        "via crawl4ai, then call this tool again."
                    ),
                },
                ResponseFormat.JSON,
            )
        return _format(cached, ResponseFormat.JSON)
    except Exception as e:
        return _err(e)


@mcp.tool(
    name="clash_list_tenure",
    annotations={"title": "List all cached tenure entries", "readOnlyHint": True, "openWorldHint": False},
)
async def clash_list_tenure(params: GetClanInput) -> str:
    """List all cached tenure entries — useful for seeing roster-wide tenure."""
    try:
        entries = list_cached_tenure()
        # Sort by total_days desc for usefulness.
        entries.sort(key=lambda e: -e.get("total_days_in_current_clan", 0))
        return _format({"count": len(entries), "entries": entries}, ResponseFormat.JSON)
    except Exception as e:
        return _err(e)


# COC API role names → display names. (admin = Elder in the in-game UI.)
_API_ROLE_DISPLAY = {
    "leader": "Leader",
    "coLeader": "Co-leader",
    "admin": "Elder",
    "member": "Member",
}


@mcp.tool(
    name="clash_refresh_tenure_roles",
    annotations={"title": "Refresh authoritative role on each cached tenure entry", "readOnlyHint": False, "openWorldHint": True},
)
async def clash_refresh_tenure_roles(params: GetClanInput) -> str:
    """Stamp each cached tenure entry with the live COC API role.

    The COS-scraped current_role can lag (briefly-promoted/demoted players, etc.).
    The COC API is the source of truth. This tool fetches the current clan
    roster (one API call) and updates every cached tenure entry's
    api_current_role field.

    Also flags:
      - Disagreements between COS-parsed role and live API role
      - Players who are no longer in the clan (kicked / left)

    Designed to be called by the scheduled snapshot task right after
    clash_snapshot_war.
    """
    try:
        client = _client()
        tag = _resolve_clan_tag(params.clan_tag)
        cached = list_cached_tenure()
        members = (await client.get_clan_members(tag)).get("items", []) or []
        members_by_tag = {m["tag"].upper(): m for m in members}

        updated = 0
        skipped_not_in_clan: list[str] = []
        disagreements: list[Dict[str, Any]] = []

        for entry in cached:
            etag = entry["tag"].upper()
            member = members_by_tag.get(etag)
            if not member:
                skipped_not_in_clan.append(entry.get("name") or etag)
                continue
            api_role_raw = member.get("role", "member")
            api_role = _API_ROLE_DISPLAY.get(api_role_raw, api_role_raw)
            old_cos = entry.get("current_role")
            if old_cos and old_cos.replace("-", "").lower() != api_role.replace("-", "").lower():
                disagreements.append({
                    "name": entry.get("name"),
                    "tag": entry["tag"],
                    "cos_role": old_cos,
                    "api_role": api_role,
                })
            update_api_role(entry["tag"], api_role)
            updated += 1

        return _format(
            {
                "cached_total": len(cached),
                "updated": updated,
                "no_longer_in_clan": skipped_not_in_clan,
                "cos_api_disagreements": disagreements,
            },
            ResponseFormat.JSON,
        )
    except Exception as e:
        return _err(e)


# --- Entrypoint ------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
