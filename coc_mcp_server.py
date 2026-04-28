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
    grade_war,
    promotion_candidates,
)
from coc_mcp.reporting import carry_forward_markdown, war_report_markdown


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
        meta = {
            "opponent_name": war.get("opponent", {}).get("name", "Opponent"),
            "result": _war_result(war, our_clan_tag),
        }
        return war_report_markdown(graded, war_meta=meta)
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

        candidates = promotion_candidates(members, aggregated, rubric)

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
                lines.append("| Name | TH | Donated | Received | Ratio | War Score | Reasons |")
                lines.append("|---|---|---|---|---|---|---|")
                for c in group_list:
                    lines.append(
                        f"| {c['name']} | {c['th']} | {c['donations']} | {c['received']} | "
                        f"{c['donation_ratio']} | {c['war_score_window']} | {'; '.join(c['reasons'])} |"
                    )
                lines.append("")
            return "\n".join(lines)
        return _format(candidates, params.response_format)
    except Exception as e:
        return _err(e)


# --- Entrypoint ------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
