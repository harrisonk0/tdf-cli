#!/usr/bin/env python3
"""
TDF MCP Server — Tour de France data for AI agents.

Provides Model Context Protocol (MCP) tools and resources for LLMs
to query live Tour de France 2026 data from four sources:
- ASO official API (results, live GPS, jerseys, stages)
- PCS via curl_cffi (individual TTT splits)
- Bluesky public API (race narrative)
- RSS feeds (news articles)

Usage:
  # Run as stdio MCP server (for Claude Desktop, Cline, etc.)
  python3 tdf_mcp.py

  # Or specify transport explicitly
  python3 tdf_mcp.py --transport stdio

Designed for integration with any MCP-compatible AI agent.
"""

import argparse
import asyncio
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Any

# Add parent dir so we can import tdf.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tdf import AsoSource, PcsSource, BlueskySource, RssSource, fmt_time, fmt_gap, truncate, YEAR

from mcp.server.fastmcp import FastMCP

# ─────────────────────────────────────────────────────────────────────────────
# Shared data sources (initialised once, reused across calls)
# ─────────────────────────────────────────────────────────────────────────────

aso = AsoSource()
pcs = PcsSource()
bsky = BlueskySource()
rss = RssSource()

# Ensure rider/team data is loaded at startup
aso.load_riders_teams()

# ─────────────────────────────────────────────────────────────────────────────
# MCP Server
# ─────────────────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "tdf-cli",
    instructions="""Tour de France 2026 data server.

Provides complete access to Tour de France data including:
- Stage results and general classification
- Live race tracking (GPS, speeds, groups)
- Jersey holders
- Bluesky social narrative feed
- News articles from cycling press
- Stage information (routes, climb profiles, checkpoints)
- Individual TTT/ITT rider splits
- Speed per segment

All times are in CET/CEST (Central European Time).
The 2026 Tour de France runs July 4-26.
""",
    debug=False,
)


# ─────────────────────────────────────────────────────────────────────────────
# Resource: stage info
# ─────────────────────────────────────────────────────────────────────────────

@mcp.resource("tdf://stages")
def get_stages() -> str:
    """List all 21 stages of the 2026 Tour de France."""
    stages = aso.load_stages()
    lines = [f"Tour de France {YEAR} - {len(stages)} Stages"]
    lines.append(f"{'Stg':>4}  {'Date':<12}  {'From':<30} {'To':<30} {'KM':>6}  {'Type'}")
    lines.append("-" * 100)
    for s in stages:
        stage = s.get("stage", 0)
        date = s.get("date", "")[:10]
        dep = s.get("departureCity", {}).get("label", "?")
        arr = s.get("arrivalCity", {}).get("label", "?")
        length = s.get("length", 0)
        stype = aso.stage_type(stage)
        lines.append(f"{stage:>4}  {date:<12}  {truncate(dep,30):<30} {truncate(arr,30):<30} {length:>6.0f}  {stype}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_stage_result(stage: int, top_n: int = 0) -> str:
    """Get stage results for a given stage.

    Args:
        stage: Stage number (1-21). Use -1 for latest completed stage.
        top_n: Number of riders to show. 0 = all riders.
    """
    if stage < 1:
        stage = aso.find_latest_stage()

    finish = aso.get_finish_rankings(stage)
    if not finish:
        return f"No results available for stage {stage}. It may not have finished yet."

    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    length = info.get("length", 0)
    stype = aso.stage_type(stage)

    lines = [f"Stage {stage}: {dep} > {arr} ({length:.1f}km, {stype})"]
    lines.append(f"{'Pos':>4}  {'Bib':>4}  {'Name':<26} {'Team':<30} {'Time':>14} {'Gap':>10}")
    lines.append("-" * 95)

    rankings = finish["rankings"]
    limit = min(top_n, len(rankings)) if top_n else len(rankings)
    for r in rankings[:limit]:
        bib = r["bib"]
        name = aso.rider_name(bib)
        team = aso.rider_team(bib)
        time_str = fmt_time(r["absolute"])
        gap_str = fmt_gap(r["relative"])
        lines.append(f"{r['position']:>4}  {bib:>4}  {truncate(name,26):<26} {truncate(team,30):<30} {time_str:>14} {gap_str:>10}")

    return "\n".join(lines)


@mcp.tool()
def get_gc(stage: int = -1, top_n: int = 10) -> str:
    """Get the general classification after a given stage.

    Args:
        stage: Stage number (1-21). Use -1 for latest completed stage.
        top_n: Number of riders to show. 0 = all 184 riders.
    """
    if stage < 1:
        stage = aso.find_latest_stage()

    finish = aso.get_finish_rankings(stage, "rankingType")
    if not finish:
        return f"GC data not yet available for stage {stage}"

    lines = [f"General Classification after Stage {stage}"]
    lines.append(f"{'Pos':>4}  {'Bib':>4}  {'Name':<26} {'Team':<30} {'Time':>14} {'Gap':>10}")
    lines.append("-" * 95)

    rankings = finish["rankings"]
    limit = min(top_n, len(rankings)) if top_n else len(rankings)
    for r in rankings[:limit]:
        bib = r["bib"]
        name = aso.rider_name(bib)
        team = aso.rider_team(bib)
        time_str = fmt_time(r["absolute"])
        gap_str = fmt_gap(r["relative"])
        lines.append(f"{r['position']:>4}  {bib:>4}  {truncate(name,26):<26} {truncate(team,30):<30} {time_str:>14} {gap_str:>10}")

    return "\n".join(lines)


@mcp.tool()
def get_jerseys() -> str:
    """Get the current jersey holders (Yellow/GC, Green/Points, Polka Dot/KOM, White/U25)."""
    tel = aso.get_telemetry()
    if not tel:
        return "Could not fetch jersey data. Live telemetry may not be available."

    ygpw = tel.get("YGPW", [])
    jersey_names = ["YELLOW (GC)", "GREEN (Points)", "POLKA DOT (KOM)", "WHITE (U25)"]
    jersey_icons = ["🟡", "🟢", "🔴", "⚪"]

    lines = [f"Tour de France {YEAR} - Jersey Holders"]
    lines.append("(from live telemetry)")
    lines.append("")

    for i in range(4):
        if i < len(ygpw) and ygpw[i]:
            r = aso._riders.get(ygpw[i])
            if r:
                lines.append(f"{jersey_icons[i]} {jersey_names[i]:<18} {r['firstname']} {r['lastname']}  ({r['team_name']}, bib {r['bib']})")

    return "\n".join(lines)


@mcp.tool()
def get_live_state() -> str:
    """Get the current live race state: rider positions, speeds, groups, and weather conditions."""
    tel = aso.get_telemetry()
    if not tel:
        return "No live telemetry available. The race may not be in progress."

    race_status = tel.get("RaceStatus", False)
    ygpw = tel.get("YGPW", [])
    riders = tel.get("Riders", [])

    jersey_icons = ["🟡", "🟢", "🔴", "⚪"]
    jersey_names = ["Yellow", "Green", "Polka", "White"]

    lines = [f"Tour de France {YEAR} - LIVE Race State"]
    lines.append(f"Race Status: {'IN PROGRESS' if race_status else 'Finished/Not Started'}")

    # Jerseys
    jersey_parts = []
    for i in range(4):
        if i < len(ygpw) and ygpw[i]:
            r = aso._riders.get(ygpw[i])
            if r:
                jersey_parts.append(f"{jersey_icons[i]}{jersey_names[i][0]}={r['firstname']} {r['lastname']}")
    if jersey_parts:
        lines.append(f"Jerseys: {'  '.join(jersey_parts)}")

    # Weather
    if riders:
        r0 = riders[0]
        lines.append(f"Conditions: {r0.get('degC', 0):.1f}°C, Wind {r0.get('kphWind', 0):.1f} kph")
        lines.append(f"Riders on course: {len(riders)}")
        lines.append("")

        # Group detection
        sorted_riders = sorted(riders, key=lambda r: r.get("kmToFinish", 999))
        groups = []
        if sorted_riders:
            cur = {"km": sorted_riders[0].get("kmToFinish", 0), "riders": [sorted_riders[0]]}
            for r in sorted_riders[1:]:
                if abs(r.get("kmToFinish", 0) - cur["km"]) < 0.15:
                    cur["riders"].append(r)
                else:
                    groups.append(cur)
                    cur = {"km": r.get("kmToFinish", 0), "riders": [r]}
            groups.append(cur)

        lines.append(f"Groups on Course ({len(groups)} groups):")
        for gi, grp in enumerate(groups):
            kphs = [r.get("kph", 0) for r in grp["riders"]]
            avg_kph = sum(kphs) / len(kphs) if kphs else 0
            names = []
            for r in grp["riders"]:
                rider = aso._riders.get(r.get("Bib"))
                if rider:
                    names.append(f"{rider['firstname']} {rider['lastname']}")
                else:
                    names.append(f"#{r.get('Bib')}")
            names_str = ", ".join(names[:5])
            if len(names) > 5:
                names_str += f" (+{len(names) - 5} more)"
            lines.append(f"  Group {gi + 1}: {grp['km']:.2f}km to finish, {len(grp['riders'])} riders, {avg_kph:.1f}kph")
            lines.append(f"    Riders: {names_str}")

    return "\n".join(lines)


@mcp.tool()
def get_bluesky_feed(query: str = "Tour de France", limit: int = 10, tag: str = "") -> str:
    """Get Bluesky social posts about the Tour de France.

    Args:
        query: Search query. Defaults to 'Tour de France'.
        limit: Maximum number of posts to return. Max 50.
        tag: Optional hashtag filter (without the # symbol).
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    posts = bsky.search(query=query, limit=min(limit, 50), since=since, tag=tag if tag else None)

    header = f"Tour de France {YEAR} - Bluesky Posts"
    if tag:
        header += f" (#{tag})"
    header += f"\nSearch: '{query}'"
    lines = [header, ""]

    for p in posts:
        record = p.get("record", {})
        author = p.get("author", {}).get("handle", "?")
        text = record.get("text", "").replace("\n", " ")[:150]
        created = record.get("createdAt", "")
        lines.append(f"  [{author}] {text}")

    if not posts:
        lines.append("  (no posts found)")

    return "\n".join(lines)


@mcp.tool()
def get_news(limit: int = 10) -> str:
    """Get the latest Tour de France news articles from cycling press (VeloNews, Escape Collective).

    Args:
        limit: Maximum number of articles to return. Max 50.
    """
    items = rss.fetch_all(tdf_only=True)
    lines = [f"Tour de France {YEAR} - News", ""]
    for item in items[:min(limit, 50)]:
        lines.append(f"  {item['source']} - {item['title']}")
        if item['description']:
            lines.append(f"    {item['description'][:200]}")
        lines.append("")

    if not items:
        lines.append("  (no news articles found)")

    return "\n".join(lines)


@mcp.tool()
def get_stage_profile(stage: int) -> str:
    """Get the climb profile for a stage: categorised climbs, altitude, length, and intermediate sprints.

    Args:
        stage: Stage number (1-21).
    """
    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    length = info.get("length", 0)
    lines = [f"Stage {stage}: {dep} > {arr} ({length:.1f}km) - Profile"]

    profile = aso.get_stage_profile(stage)
    if not profile:
        return f"No profile data available for stage {stage}."

    cat_map = {"H": "HC", "1": "Cat 1", "2": "Cat 2", "3": "Cat 3", "4": "Cat 4", "X": "Climb"}

    for entry in profile:
        if entry["type"] == "climb":
            cat = cat_map.get(entry.get("code", ""), "Climb")
            lines.append(f"  {cat:<6} at km {entry['km']:<6.1f}  {entry['name']:<40}  {entry['altitude']:>4.0f}m  length: {entry['length']:.0f}m")
        elif entry["type"] == "chrono":
            lines.append(f"  {'CHRONO':<6} at km {entry['km']:<6.1f}  {entry['name']}")

    if not profile:
        lines.append("  No categorised climbs on this stage.")

    return "\n".join(lines)


@mcp.tool()
def get_ttt_splits(stage: int) -> str:
    """Get individual rider splits for a TTT or ITT stage from ProCyclingStats.

    Shows each rider's gap to their team's fastest finisher.

    Args:
        stage: Stage number (1-21), should be a TTT or ITT stage.
    """
    splits = pcs.get_ttt_splits(stage)
    if not splits:
        return f"Individual splits not available for stage {stage}. (PCS may be unreachable or not a TTT/ITT stage.)"

    lines = [f"Individual TTT Splits — Stage {stage}", ""]
    for team in splits:
        lines.append(f"  {team['team']}")
        for r in team["riders"]:
            gap = r["gap"]
            marker = "  " if gap == "winner" else gap
            lines.append(f"    {r['firstname']:12s} {r['lastname']:20s} {marker}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def get_speed_segments(stage: int) -> str:
    """Get average speed per segment for a stage from PCS statistics. Shows how speed changes across the route.

    Args:
        stage: Stage number (1-21).
    """
    segments = pcs.get_speed_segments(stage)
    if not segments:
        return f"Speed data not available for stage {stage}."

    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    lines = [f"Stage {stage}: {dep} > {arr} - Speed per Segment", ""]
    lines.append(f"{'Segment (km)':>15}  {'Riders':>7}  {'Avg Speed'}")
    lines.append("-" * 40)
    for seg in segments:
        lines.append(f"{seg['segment']:>15}  {seg['riders']:>7}  {seg['speed']:>9.1f} kph")

    return "\n".join(lines)


@mcp.tool()
def get_checkpoints(stage: int) -> str:
    """Get checkpoint locations with road names, schedules, and climb info for a stage.

    Args:
        stage: Stage number (1-21).
    """
    cps = aso.get_checkpoints(stage)
    if not cps:
        return f"No checkpoint data for stage {stage}"

    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    lines = [f"Stage {stage}: {dep} > {arr} - Checkpoints ({len(cps)})"]
    lines.append(f"{'CP':>4}  {'KM':>7}  {'Type':<8}  {'Road':<35}  {'Place':<20}  {'Schedule':<12}  {'Climb'}")
    lines.append("-" * 120)

    for cp in cps:
        cp_num = cp.get("checkpoint", "?")
        length = cp.get("length", 0)
        road = cp.get("road", "")
        place = cp.get("place", "")
        sched = cp.get("middleSchedule", "")

        type_str = ""
        for ct in cp.get("checkpointTypes", []):
            type_str += ct.get("code", "")

        climb = ""
        for s in cp.get("checkpointSummits", []):
            sinfo = s.get("summit", {})
            climb = f"{sinfo.get('name', '')} ({sinfo.get('altitude', 0):.0f}m, {s.get('length', 0):.0f}m)"

        lines.append(f"{cp_num:>4}  {length:>7.1f}  {type_str:<8}  {truncate(road,35):<35}  {truncate(place,20):<20}  {sched:<12}  {climb}")

    return "\n".join(lines)


@mcp.tool()
def search_riders(query: str) -> str:
    """Search for a rider by name (first or last). Returns bib number, team, and nationality.

    Args:
        query: Full or partial rider name to search for.
    """
    query_lower = query.lower()
    riders_list = list(aso._riders.values()) if aso._riders else []
    matches = [
        r for r in riders_list
        if query_lower in r["firstname"].lower() or query_lower in r["lastname"].lower()
    ]

    if not matches:
        return f"No riders found matching '{query}'."

    lines = [f"Riders matching '{query}':", ""]
    lines.append(f"{'Bib':>4}  {'Name':<26}  {'Nat':>4}  {'Team'}")
    lines.append("-" * 60)
    for r in sorted(matches, key=lambda x: x["bib"]):
        name = f"{r['firstname']} {r['lastname']}".strip()
        lines.append(f"{r['bib']:>4}  {truncate(name,26):<26}  {r['nationality']:>4}  {r.get('team_name', '')}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TDF MCP Server — Tour de France data for AI agents")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio",
                        help="MCP transport (default: stdio)")
    parser.add_argument("--host", default="127.0.0.1", help="Host for SSE transport")
    parser.add_argument("--port", type=int, default=8000, help="Port for SSE transport")
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
