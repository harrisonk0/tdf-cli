#!/usr/bin/env python3
"""MCP server for Tour de France 2026 - results, live GPS, narrative, news."""

import argparse
import asyncio
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tdf import AsoSource, PcsSource, BlueskySource, RssSource, fmt_time, fmt_gap, truncate, YEAR, format_rankings_table, format_jerseys, format_stages, format_teams, format_stage_profile

from mcp.server.fastmcp import FastMCP
import requests as http_requests

aso = AsoSource()
pcs = PcsSource()
bsky = BlueskySource()
rss = RssSource()
mcp = FastMCP(
    "tdf-cli",
    instructions="""Tour de France 2026 data for AI agents.

Sources: ASO official API (results, live GPS), PCS (individual TTT splits via curl_cffi),
Bluesky public API (race narrative), RSS feeds (news).

Times in CET/CEST (ASO timing system operates in Central European Time for TDF).""",
    debug=False,
)


@mcp.resource("tdf://stages")
def get_stages() -> str:
    return format_stages(aso)


@mcp.tool()
def get_stage_result(stage: int, top_n: int = 0) -> str:
    """Stage results: times and gaps. stage=-1 for latest."""
    if stage < 1:
        stage = aso.find_latest_stage()
    finish = aso.get_finish_rankings(stage, finish_type="ite")
    if not finish:
        return f"Stage {stage} hasn't happened yet"

    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    length = info.get("length", 0)
    stype = aso.stage_type(stage)

    lines = [f"Stage {stage}: {dep} > {arr} ({length:.1f}km, {stype})"]
    lines.append(format_rankings_table(aso, finish.get("rankings", []), top_n))
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
    lines.append(format_rankings_table(aso, finish.get("rankings", []), top_n))
    return "\n".join(lines)


@mcp.tool()
def get_jerseys() -> str:
    """Current jersey holders: Yellow, Green, Polka Dot, White."""
    tel = aso.get_telemetry()
    if not tel:
        return "No jersey data right now (ASO telemetry unavailable)"
    return format_jerseys(aso, tel)


@mcp.tool()
def get_live_state() -> str:
    """Live race state: GPS, groups, speeds, weather."""
    tel = aso.get_telemetry()
    if not tel:
        return "No live data - race probably not in progress (ASO API returned empty)"

    race_status = tel.get("RaceStatus", False)
    ygpw = tel.get("YGPW", [])
    riders = aso.clean_telemetry(tel)

    jersey_icons = ["🟡", "🟢", "🔴", "⚪"]
    jersey_names = ["Yellow", "Green", "Polka", "White"]

    lines = [f"Tour de France {YEAR} - Live"]
    lines.append(f"Status: {'IN PROGRESS' if race_status else 'Finished/Not Started'}")

    jersey_parts = []
    for i in range(4):
        if i < len(ygpw) and ygpw[i]:
            r = aso.get_rider(ygpw[i])
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
                rider = aso.get_rider(r.get("Bib"))
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
    aso.load_riders_teams()
    tel = aso.get_telemetry()
    if not tel:
        return "No live data - race probably not in progress (ASO API returned empty)"

    riders = aso.clean_telemetry(tel)

    if not riders:
        return "No riders with valid GPS positions"

    sorted_riders = sorted(riders, key=lambda r: r.get("kmToFinish", 999))
    leader_km = sorted_riders[0].get("kmToFinish", 0)

    # Match names
    matches = []
    for name in rider_names:
        q = name.lower().replace(" ", "")
        found_any = False
        for bib, info in aso.get_all_riders().items():
            full = f"{info['firstname']}{info['lastname']}".lower().replace(" ", "")
            if q in full:
                matches.append((bib, info))
                found_any = True
        if not found_any:
            matches.append((None, {"firstname": name, "lastname": "(not found)", "team_code": "?"}))

    tel_by_bib = {r.get("Bib"): r for r in riders}
    lines = [f"Tour de France {YEAR} - Rider Positions"]
    lines.append(f"Leader: {(aso.get_rider(sorted_riders[0].get('Bib')) or {}).get('lastname', '?')} at {leader_km:.2f}km")
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
    return format_stage_profile(aso, stage)


@mcp.tool()
def get_ttt_splits(stage: int) -> str:
    """Individual per-rider splits for TTT/ITT stages (from PCS)."""
    splits = pcs.get_ttt_splits(stage)
    if not splits:
        session = pcs._get_session()
        reason = "curl_cffi not loaded" if session is None else "PCS returned no data or stage is not a TTT/ITT"
        return f"Individual splits not available for stage {stage} ({reason})"
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
    riders_list = list(aso.get_all_riders().values())
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
    return format_teams(aso)


@mcp.tool()
def get_stage_checkpoint_splits(stage: int, top_n: int = 10) -> str:
    """How time gaps evolved across the stage - splits at each checkpoint."""

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
        rider = (aso.get_rider(r["bib"]) or {})
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


# Known coordinates for key TDF locations
LOCATION_COORDS = {
    # Stage starts/finishes
    "granollers": (41.608, 2.288),
    "les angles": (42.573, 2.068),
    "lille": (50.629, 3.057),
    "london": (51.507, -0.128),
    "bordeaux": (44.837, -0.579),
    "toulouse": (43.604, 1.444),
    "montpellier": (43.610, 3.876),
    "nice": (43.710, 7.262),
    "paris": (48.856, 2.352),
    # Key climbs
    "col de toses": (42.353, 2.018),
    "col du galibier": (45.064, 6.408),
    "col du tourmalet": (42.908, 0.145),
    "alpe d'huez": (45.092, 6.071),
    "mont ventoux": (44.174, 5.276),
    "col d'aubisque": (42.973, -0.165),
    "col du calvaire": (42.504, 2.038),
    "font-romeu": (42.504, 2.038),
    "col de la madeleine": (45.435, 6.372),
    "col de la croix de fer": (45.229, 6.201),
    "col de l'izeran": (45.311, 6.532),
    "col du platzerwasel": (47.949, 7.034),
    "col du firstplan": (47.966, 7.048),
    "hautacam": (42.917, -0.018),
    "luz-ardiden": (42.871, 0.003),
    "pic du midi": (42.937, 0.141),
}


@mcp.tool()
def get_route_weather(stage: int) -> str:
    """Weather forecast at key points along a stage route (Open-Meteo API)."""
    info = aso.stage_info(stage)
    if not info:
        return f"No info for stage {stage}"
    
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    
    # Get coordinates for start and finish
    dep_key = dep.lower().split(",")[0].strip()
    arr_key = arr.lower().split(",")[0].strip()
    
    start_coords = None
    finish_coords = None
    for name, coords in LOCATION_COORDS.items():
        if name == dep_key:
            start_coords = coords
        if name == arr_key:
            finish_coords = coords
    
    if not start_coords or not finish_coords:
        return f"Coordinates not available for {dep} > {arr}. Known locations: {', '.join(sorted(LOCATION_COORDS.keys())[:20])}..."
    
    wmo_desc = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
                45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
                55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
                71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Light showers",
                81: "Showers", 82: "Heavy showers", 95: "Thunderstorm", 96: "T-storm hail"}
    
    # Get climb coordinates if available
    climbs = []
    for entry in aso.get_stage_profile(stage):
        if entry["type"] == "climb":
            climb_name = entry["name"].lower()
            for name, coords in LOCATION_COORDS.items():
                if name in climb_name:
                    # Use the short name from our coords dict, not the full ASO name
                    climbs.append((name.title(), coords, entry["altitude"]))
                    break
    
    lines = [f"Stage {stage}: {dep} > {arr} - Route Weather"]
    lines.append("")
    
    # Check weather at each point
    points = [(f"{dep} (Start)", start_coords)]
    for name, coords, alt in climbs:
        points.append((f"{name} ({alt:.0f}m)", coords))
    points.append((f"{arr} (Finish)", finish_coords))
    
    for name, (lat, lon) in points:
        try:
            url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,wind_speed_10m,wind_direction_10m,weather_code&timezone=Europe/Paris"
            resp = http_requests.get(url, timeout=10)
            data = resp.json()
            current = data.get("current", {})
            temp = current.get("temperature_2m", "?")
            wind = current.get("wind_speed_10m", "?")
            wind_dir = current.get("wind_direction_10m", 0)
            wmo = current.get("weather_code", 0)
            desc = wmo_desc.get(wmo, f"Code {wmo}")
            
            # Wind direction as compass
            dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
            compass = dirs[int((wind_dir + 22.5) / 45) % 8]
            
            lines.append(f"  {name:<35} {temp:>5.1f}C  Wind {wind:>5.1f}kph {compass}  {desc}")
        except Exception as e:
            lines.append(f"  {name:<35} Weather data unavailable")
    
    return "\n".join(lines)


@mcp.tool()
def get_stage_schedule(stage: int) -> str:
    """Scheduled times at key checkpoints for a stage (race organisation estimates)."""
    info = aso.stage_info(stage)
    if not info:
        return f"No info for stage {stage}"
    
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    length = info.get("length", 0)
    stype = aso.stage_type(stage)
    
    cps = aso.get_checkpoints(stage)
    if not cps:
        return f"No checkpoints for stage {stage}"
    
    lines = [f"Stage {stage}: {dep} > {arr} ({length:.1f}km, {stype}) - Schedule"]
    lines.append("")
    lines.append(f"{'KM':>7}  {'Time':>8}  {'Place'}")
    lines.append("-" * 60)
    
    for cp in cps:
        km = cp.get("length", 0)
        sched = cp.get("middleSchedule", "")
        place = cp.get("place", "")
        # Only show key checkpoints (start, feed zones, summits, finish)
        types = [ct.get("code", "") for ct in cp.get("checkpointTypes", [])]
        is_summit = bool(cp.get("checkpointSummits"))
        is_start = "F" in types or "R" in types
        is_feed = "N" in types
        is_finish = "NA" in types
        
        if is_start or is_feed or is_summit or is_finish or km == 0 or km >= length - 1:
            marker = ""
            if is_feed:
                marker = " [FEED]"
            if is_summit:
                s = cp.get("checkpointSummits", [{}])[0]
                sname = s.get("summit", {}).get("name", "")
                alt = s.get("summit", {}).get("altitude", 0)
                marker = f" [SUMMIT: {sname} {alt:.0f}m]"
            if is_finish:
                marker = " [FINISH]"
            
            lines.append(f"{km:>7.1f}  {sched:>8}  {place}{marker}")
    
    return "\n".join(lines)


@mcp.tool()
def get_intermediate_sprints(stage: int) -> str:
    """Intermediate sprint results for a stage. Returns top finishers at each sprint point."""
    data = aso.get_rankings(stage, "rankingType")
    if not data:
        return f"No intermediate sprint data for stage {stage}"

    # Find intermediate sprint entries (type "ipe")
    sprints = [c for c in data if c.get("type") == "ipe"]
    if not sprints:
        # Try alternative: check for "im" type (some stages use this)
        sprints = [c for c in data if c.get("type", "").startswith("im")]
    if not sprints:
        return f"No intermediate sprints recorded for stage {stage}"

    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")

    lines = [f"Stage {stage}: {dep} > {arr} - Intermediate Sprints"]

    for sprint in sprints:
        km = sprint.get("length", 0)
        cp = sprint.get("checkpoint", "?")
        rankings = sprint.get("rankings", [])
        if not rankings:
            continue
        lines.append(f"\nSprint at km {km:.1f} (CP {cp}):")
        lines.append(f"{'Pos':>4}  {'Bib':>4}  {'Name':<26} {'Team':<30} {'Bonus':>5}")
        lines.append("-" * 75)
        for r in rankings[:10]:
            bib = r["bib"]
            name = aso.rider_name(bib)
            team = aso.rider_team(bib)
            bonus = r.get("bonus", 0)
            lines.append(f"{r['position']:>4}  {bib:>4}  {truncate(name,26):<26} {truncate(team,30):<30} {bonus:>5}")

    return "\n".join(lines)


@mcp.tool()
def get_kom_standings(stage: int = -1, top_n: int = 10) -> str:
    """Polka dot jersey / KOM standings. stage=-1 for latest."""
    if stage < 1:
        stage = aso.find_latest_stage()
    data = aso.get_rankings(stage, "rankingType")
    if not data:
        return f"No KOM data for stage {stage}"

    # Mountains general classification (type "img")
    kom = [c for c in data if c.get("type") == "img"]
    if not kom:
        return f"No KOM standings available for stage {stage}"

    kom = kom[0]
    rankings = kom.get("rankings", [])
    if not rankings:
        return f"No KOM rankings yet for stage {stage}"

    lines = [f"🔴 Polka Dot Jersey (KOM) Standings after Stage {stage}"]
    lines.append(f"{'Pos':>4}  {'Bib':>4}  {'Name':<26} {'Team':<30} {'Points':>6}")
    lines.append("-" * 75)

    limit = min(top_n, len(rankings))
    for r in rankings[:limit]:
        bib = r["bib"]
        name = aso.rider_name(bib)
        team = aso.rider_team(bib)
        points = r.get("absolute", 0)
        lines.append(f"{r['position']:>4}  {bib:>4}  {truncate(name,26):<26} {truncate(team,30):<30} {points:>6}")

    return "\n".join(lines)


@mcp.tool()
def get_points_standings(stage: int = -1, top_n: int = 10) -> str:
    """Green jersey / points classification standings. stage=-1 for latest."""
    if stage < 1:
        stage = aso.find_latest_stage()
    data = aso.get_rankings(stage, "rankingType")
    if not data:
        return f"No points data for stage {stage}"

    # Points general classification (type "ipg")
    pts = [c for c in data if c.get("type") == "ipg"]
    if not pts:
        return f"No points standings available for stage {stage}"

    pts = pts[0]
    rankings = pts.get("rankings", [])
    if not rankings:
        return f"No points rankings yet for stage {stage}"

    lines = [f"🟢 Green Jersey (Points) Standings after Stage {stage}"]
    lines.append(f"{'Pos':>4}  {'Bib':>4}  {'Name':<26} {'Team':<30} {'Points':>6}")
    lines.append("-" * 75)

    limit = min(top_n, len(rankings))
    for r in rankings[:limit]:
        bib = r["bib"]
        name = aso.rider_name(bib)
        team = aso.rider_team(bib)
        points = r.get("absolute", 0)
        lines.append(f"{r['position']:>4}  {bib:>4}  {truncate(name,26):<26} {truncate(team,30):<30} {points:>6}")

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
