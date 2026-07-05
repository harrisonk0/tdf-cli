#!/usr/bin/env python3
"""MCP server for Tour de France 2026 - results, live GPS, narrative, news."""

import argparse
import asyncio
import json
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tdf import AsoSource, PcsSource, BlueskySource, RssSource, fmt_time, fmt_gap, truncate, YEAR

from mcp.server.fastmcp import FastMCP

aso = AsoSource()
pcs = PcsSource()
bsky = BlueskySource()
rss = RssSource()
aso.load_riders_teams()

mcp = FastMCP(
    "tdf-cli",
    instructions="""Tour de France 2026 data for AI agents.

Sources: ASO official API (results, live GPS), PCS (individual TTT splits via curl_cffi),
Bluesky public API (race narrative), RSS feeds (news).

Times in CET/CEST.""",
    debug=False,
)


@mcp.resource("tdf://stages")
def get_stages() -> str:
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


@mcp.tool()
def get_stage_result(stage: int, top_n: int = 0) -> str:
    """Stage results: times and gaps. stage=-1 for latest."""
    if stage < 1:
        stage = aso.find_latest_stage()
    finish = aso.get_finish_rankings(stage)
    if not finish:
        return f"Stage {stage} hasn't happened yet"

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
    """General classification after a stage. stage=-1 for latest."""
    if stage < 1:
        stage = aso.find_latest_stage()
    finish = aso.get_finish_rankings(stage, "rankingType")
    if not finish:
        return f"No GC data for stage {stage}"

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
    """Current jersey holders: Yellow, Green, Polka Dot, White."""
    tel = aso.get_telemetry()
    if not tel:
        return "No jersey data right now"

    ygpw = tel.get("YGPW", [])
    jersey_names = ["YELLOW (GC)", "GREEN (Points)", "POLKA DOT (KOM)", "WHITE (U25)"]
    jersey_icons = ["🟡", "🟢", "🔴", "⚪"]

    lines = [f"Tour de France {YEAR} - Jerseys"]
    lines.append("(live telemetry - mid-race leaders, not final GC)")
    lines.append("")
    for i in range(4):
        if i < len(ygpw) and ygpw[i]:
            r = aso._riders.get(ygpw[i])
            if r:
                lines.append(f"{jersey_icons[i]} {jersey_names[i]:<18} {r['firstname']} {r['lastname']}  ({r['team_name']}, bib {r['bib']})")
    return "\n".join(lines)


@mcp.tool()
def get_live_state() -> str:
    """Live race state: GPS, groups, speeds, weather."""
    tel = aso.get_telemetry()
    if not tel:
        return "No live data - race probably not in progress"

    race_status = tel.get("RaceStatus", False)
    ygpw = tel.get("YGPW", [])
    raw_riders = tel.get("Riders", [])

    # --- Data clean-up: ASO API often returns duplicate entries + stale GPS ---

    # 1. Deduplicate by bib (API sometimes returns each rider twice)
    seen_bibs = set()
    riders = []
    for r in raw_riders:
        bib = r.get("Bib")
        if bib and bib not in seen_bibs:
            seen_bibs.add(bib)
            riders.append(r)

    # 2. Filter out impossible GPS positions: find today's stage length, drop
    #    entries whose kmToFinish exceeds it (stale pre-start positions).
    from datetime import datetime
    stage_length = None
    today = datetime.now().strftime("%Y-%m-%d")
    for s in aso.load_stages():
        if s.get("date", "")[:10] == today:
            stage_length = s.get("length", 0)
            break
    if stage_length is not None and stage_length > 0:
        riders = [r for r in riders if 0 <= r.get("kmToFinish", 0) <= stage_length + 2.0]

    jersey_icons = ["🟡", "🟢", "🔴", "⚪"]
    jersey_names = ["Yellow", "Green", "Polka", "White"]

    lines = [f"Tour de France {YEAR} - Live"]
    lines.append(f"Status: {'IN PROGRESS' if race_status else 'Finished/Not Started'}")

    jersey_parts = []
    for i in range(4):
        if i < len(ygpw) and ygpw[i]:
            r = aso._riders.get(ygpw[i])
            if r:
                jersey_parts.append(f"{jersey_icons[i]}{jersey_names[i][0]}={r['firstname']} {r['lastname']}")
    if jersey_parts:
        lines.append(f"Jerseys: {'  '.join(jersey_parts)}")

    if riders:
        r0 = riders[0]
        lines.append(f"Weather: {r0.get('degC', 0):.1f}°C, Wind {r0.get('kphWind', 0):.1f} kph")
        lines.append(f"Riders on course: {len(riders)}")
        lines.append("")

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

        lines.append(f"Groups ({len(groups)}):")
        for gi, grp in enumerate(groups):
            kphs = [r.get("kph", 0) for r in grp["riders"]]
            avg_kph = sum(kphs) / len(kphs) if kphs else 0
            names = []
            for r in grp["riders"]:
                rider = aso._riders.get(r.get("Bib"))
                names.append(f"{rider['firstname']} {rider['lastname']}" if rider else f"#{r.get('Bib')}")
            names_str = ", ".join(names[:5])
            if len(names) > 5:
                names_str += f" (+{len(names) - 5})"
            lines.append(f"  Group {gi+1}: {grp['km']:.2f}km out, {len(grp['riders'])} riders, {avg_kph:.1f}kph")
            lines.append(f"    {names_str}")

    return "\n".join(lines)


@mcp.tool()
def get_rider_positions(rider_names: list[str]) -> str:
    """Live GPS positions for specific riders by name. Pass one or more names (e.g. ['Pogacar', 'Vingegaard'])."""
    global aso
    aso.load_riders_teams()
    tel = aso.get_telemetry()
    if not tel:
        return "No live data - race probably not in progress"

    raw_riders = tel.get("Riders", [])
    # Dedup by bib
    seen_bibs = set()
    riders = []
    for r in raw_riders:
        bib = r.get("Bib")
        if bib and bib not in seen_bibs:
            seen_bibs.add(bib)
            riders.append(r)
    # Filter bad GPS
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    stage_length = None
    for s in aso.load_stages():
        if s.get("date", "")[:10] == today:
            stage_length = s.get("length", 0)
            break
    if stage_length is not None and stage_length > 0:
        riders = [r for r in riders if 0 <= r.get("kmToFinish", 0) <= stage_length + 2.0]

    if not riders:
        return "No riders with valid GPS positions"

    sorted_riders = sorted(riders, key=lambda r: r.get("kmToFinish", 999))
    leader_km = sorted_riders[0].get("kmToFinish", 0)

    # Match names
    matches = []
    for name in rider_names:
        q = name.lower().replace(" ", "")
        found_any = False
        for bib, info in aso._riders.items():
            full = f"{info['firstname']}{info['lastname']}".lower()
            if q in full:
                matches.append((bib, info))
                found_any = True
        if not found_any:
            matches.append((None, {"firstname": name, "lastname": "(not found)", "team_code": "?"}))

    tel_by_bib = {r.get("Bib"): r for r in riders}
    lines = [f"Tour de France {YEAR} - Rider Positions"]
    lines.append(f"Leader: {aso._riders.get(sorted_riders[0].get('Bib'), {}).get('lastname', '?')} at {leader_km:.2f}km")
    lines.append("")
    lines.append(f"{'Bib':>4}  {'Name':<26} {'Team':<22} {'kmToFin':>8} {'Gap':>6} {'Speed':>6} {'Grad%':>5} {'Status':>8}")
    lines.append("-" * 90)
    for bib, info in matches:
        if bib is None:
            lines.append(f"{'':>4}  {info['firstname']:<26} {'—':<22} {'NOT FOUND':>8}")
            continue
        entry = tel_by_bib.get(bib)
        if entry:
            km = entry.get("kmToFinish", 0)
            gap = km - leader_km
            lines.append(f"{bib:>4}  {info['firstname'] + ' ' + info['lastname']:<26} "
                        f"{aso._teams.get(info['team_code'], {}).get('name', info['team_code']):<22} "
                        f"{km:>8.2f} {gap:>+6.2f} "
                        f"{entry.get('kph', 0):>6.1f} {entry.get('Gradient', 0):>5.1f} "
                        f"{entry.get('Status', 'unknown'):>8}")
        else:
            lines.append(f"{bib:>4}  {info['firstname'] + ' ' + info['lastname']:<26} "
                        f"{aso._teams.get(info['team_code'], {}).get('name', info['team_code']):<22} "
                        f"{'NO GPS':>8} {'':>6} {'':>6} {'':>5} {'not tracked':>8}")
    return "\n".join(lines)


@mcp.tool()
def get_bluesky_feed(query: str = "Tour de France", limit: int = 10, tag: str = "") -> str:
    """Bluesky posts about the Tour. Optional hashtag filter (no #)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    posts = bsky.search(query=query, limit=min(limit, 50), since=since, tag=tag if tag else None)

    header = f"Tour de France {YEAR} - Bluesky"
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
        lines.append("  (nothing found)")
    return "\n".join(lines)


@mcp.tool()
def get_news(limit: int = 10) -> str:
    """Latest Tour de France news from VeloNews and Escape Collective."""
    items = rss.fetch_all(tdf_only=True)
    lines = [f"Tour de France {YEAR} - News", ""]
    for item in items[:min(limit, 50)]:
        lines.append(f"  {item['source']} - {item['title']}")
        if item['description']:
            lines.append(f"    {item['description'][:200]}")
        lines.append("")
    if not items:
        lines.append("  (no articles right now)")
    return "\n".join(lines)


@mcp.tool()
def get_stage_profile(stage: int) -> str:
    """Climb profile: categories, altitude, length, intermediate sprints."""
    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    length = info.get("length", 0)
    lines = [f"Stage {stage}: {dep} > {arr} ({length:.1f}km) - Profile"]

    profile = aso.get_stage_profile(stage)
    if not profile:
        return f"No profile data for stage {stage}"

    cat_map = {"H": "HC", "1": "Cat 1", "2": "Cat 2", "3": "Cat 3", "4": "Cat 4", "X": "Climb"}
    for entry in profile:
        if entry["type"] == "climb":
            cat = cat_map.get(entry.get("code", ""), "Climb")
            lines.append(f"  {cat:<6} at km {entry['km']:<6.1f}  {entry['name']:<40}  {entry['altitude']:>4.0f}m  length: {entry['length']:.0f}m")
        elif entry["type"] == "chrono":
            lines.append(f"  {'CHRONO':<6} at km {entry['km']:<6.1f}  {entry['name']}")
    if profile:
        n_climbs = sum(1 for e in profile if e["type"] == "climb")
        if n_climbs == 0:
            lines.append("  No categorised climbs")
    return "\n".join(lines)


@mcp.tool()
def get_ttt_splits(stage: int) -> str:
    """Individual per-rider splits for TTT/ITT stages (from PCS)."""
    splits = pcs.get_ttt_splits(stage)
    if not splits:
        return f"Individual splits not available for stage {stage}"

    lines = [f"TTT Splits - Stage {stage}", ""]
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
    """Average speed per segment from PCS - shows how pace changes across the route."""
    segments = pcs.get_speed_segments(stage)
    if not segments:
        return f"No speed data for stage {stage}"

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
    """Checkpoint locations with road names, schedules, and climbs."""
    cps = aso.get_checkpoints(stage)
    if not cps:
        return f"No checkpoints for stage {stage}"

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
        type_str = "".join(ct.get("code", "") for ct in cp.get("checkpointTypes", []))
        climb = ""
        for s in cp.get("checkpointSummits", []):
            sinfo = s.get("summit", {})
            climb = f"{sinfo.get('name', '')} ({sinfo.get('altitude', 0):.0f}m, {s.get('length', 0):.0f}m)"
        lines.append(f"{cp_num:>4}  {length:>7.1f}  {type_str:<8}  {truncate(road,35):<35}  {truncate(place,20):<20}  {sched:<12}  {climb}")
    return "\n".join(lines)


@mcp.tool()
def search_riders(query: str) -> str:
    """Find a rider by name, returns bib, team, nationality."""
    query_lower = query.lower()
    riders_list = list(aso._riders.values()) if aso._riders else []
    matches = [r for r in riders_list
               if query_lower in r["firstname"].lower() or query_lower in r["lastname"].lower()]

    if not matches:
        return f"Nobody found matching '{query}'"

    lines = [f"Riders matching '{query}':", ""]
    lines.append(f"{'Bib':>4}  {'Name':<26}  {'Nat':>4}  {'Team'}")
    lines.append("-" * 60)
    for r in sorted(matches, key=lambda x: x["bib"]):
        name = f"{r['firstname']} {r['lastname']}".strip()
        lines.append(f"{r['bib']:>4}  {truncate(name,26):<26}  {r['nationality']:>4}  {r.get('team_name', '')}")
    return "\n".join(lines)


@mcp.tool()
def get_teams() -> str:
    """All 23 teams with codes and nationalities."""
    _, teams = aso.load_riders_teams()
    team_list = sorted(teams.values(), key=lambda t: t.get("name", ""))
    lines = [f"Tour de France {YEAR} - Teams ({len(team_list)})"]
    lines.append(f"{'#':>3}  {'Team Name':<40}  {'Code':<6}  {'Country'}")
    lines.append("-" * 65)
    for i, t in enumerate(team_list, 1):
        lines.append(f"{i:>3}  {truncate(t.get('name',''),40):<40}  {t.get('code',''):<6}  {t.get('nationality','')}")
    return "\n".join(lines)


@mcp.tool()
def get_stage_checkpoint_splits(stage: int, top_n: int = 10) -> str:
    """How time gaps evolved across the stage - splits at each checkpoint."""
    from tdf import fmt_time, fmt_gap

    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    lines = [f"Stage {stage}: {dep} > {arr} - Checkpoint Splits"]

    cps = aso.get_rankings(stage)
    if len(cps) < 2:
        return f"No checkpoint splits for stage {stage}"

    cps.sort(key=lambda c: c.get("length", 0))
    finish_cp = max(cps, key=lambda c: c.get("length", 0))
    top_n = min(top_n, len(finish_cp["rankings"]))

    hdr = f"{'CP':>4}  {'KM':>6}"
    for r in finish_cp["rankings"][:top_n]:
        rider = aso._riders.get(r["bib"], {})
        ln = rider.get("lastname", f"#{r['bib']}")
        hdr += f" {truncate(ln,14):>14}"
    lines.append(hdr)

    for cp in cps:
        row = f"CP{cp['checkpoint']:>3}  {cp.get('length',0):>6.1f}"
        for hdr_r in finish_cp["rankings"][:top_n]:
            found = next((r for r in cp["rankings"] if r["bib"] == hdr_r["bib"]), None)
            if found:
                gap = fmt_gap(found["relative"])
                if gap:
                    row += f" {gap:>14}"
                elif found["relative"] == 0:
                    t = fmt_time(found["absolute"])
                    row += f" {t[3:]:>14}"
                else:
                    row += f" {'0':>14}"
            else:
                row += f" {'-':>14}"
        lines.append(row)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="TDF MCP server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="SSE host")
    parser.add_argument("--port", type=int, default=8000, help="SSE port")
    args = parser.parse_args()

    if args.transport == "sse":
        # Render sets $PORT env var instead of --port flag
        env_port = os.environ.get("PORT")
        if env_port:
            try:
                args.port = int(env_port)
            except ValueError:
                pass
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.settings.debug = False
        # Clear DNS rebinding protection for Render/Cloudflare
        mcp.settings.transport_security = None
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
