#!/usr/bin/env python3
"""Tour de France 2026 CLI - results, GPS, Bluesky, PCS splits."""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from html import unescape
from xml.etree import ElementTree as ET
from pathlib import Path

import requests

ASO_BASE = "https://racecenter.letour.fr/api"
YEAR = 2026  # Update annually
# ASO API type codes:
#   itg = individual time general (GC cumulative)
#   ite = individual time event (stage finish result)
#   ipe = intermediate points event (sprint)
#   ipg = intermediate points general (points classification)
#   img = intermediate mountains general (KOM classification)
#   YGPW = [Yellow, Green, Polka, White] jersey bib indices
# Stage types: EQU=TTT, IND=ITT, HMG=Mountain, MOG=Road, PAS=Mountain, PLN=Flat, VAL=Road
CACHE_DIR = Path.home() / ".tdf_cache"
CACHE_TTL = 3600


class AsoSource:
    def __init__(self):
        self._riders = None
        self._teams = None
        self._stages = None

    def _get(self, path, timeout=15):
        r = requests.get(f"{ASO_BASE}/{path}",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=timeout)
        if r.status_code == 204 or not r.text:
            return None
        r.raise_for_status()
        return r.json()

    def load_stages(self):
        if self._stages is not None:
            return self._stages
        data = self._get(f"stage-{YEAR}")
        self._stages = sorted(data, key=lambda s: s.get("stage", 0)) if data else []
        return self._stages

    def load_riders_teams(self):
        if self._riders is not None:
            return self._riders, self._teams
        data = self._get(f"allCompetitors-{YEAR}")
        self._riders = {}
        self._teams = {}
        if data:
            for item in data:
                if "bib" in item:
                    bib = item["bib"]
                    team_id = item.get("$team", "")
                    team_hash = team_id.split(":", 1)[1] if ":" in team_id else team_id
                    self._riders[bib] = {
                        "bib": bib,
                        "firstname": item.get("firstname", ""),
                        "lastname": item.get("lastname", ""),
                        "nationality": item.get("nationality", ""),
                        "team_id": team_hash,
                    }
                else:
                    tid = item.get("_id", "")
                    self._teams[tid] = {
                        "name": item.get("name", ""),
                        "code": item.get("code", ""),
                        "nationality": item.get("nationality", ""),
                        "_id": tid,
                    }
        team_data = self._get(f"team-{YEAR}")
        if team_data:
            self._teams = {t["_id"]: t for t in team_data if "_id" in t}
        for bib, rider in self._riders.items():
            team = self._teams.get(rider["team_id"])
            if team:
                rider["team_name"] = team.get("name", "")
                rider["team_code"] = team.get("code", "")
            else:
                rider["team_name"] = ""
                rider["team_code"] = ""
        return self._riders, self._teams

    def rider_name(self, bib):
        if self._riders is None:
            self.load_riders_teams()
        r = self._riders.get(bib)
        return f"{r['firstname']} {r['lastname']}".strip() if r else f"Bib #{bib}"

    def rider_team(self, bib):
        if self._riders is None:
            self.load_riders_teams()
        r = self._riders.get(bib)
        return r["team_name"] if r else ""

    def find_latest_stage(self):
        stages = self.load_stages()
        for s in reversed(stages):
            snum = s.get("stage")
            if snum and self.get_finish_rankings(snum):
                return snum
        return 1

    def stage_info(self, stage_num):
        stages = self.load_stages()
        for s in stages:
            if s.get("stage") == stage_num:
                return s
        return {}

    def stage_type(self, stage_num):
        info = self.stage_info(stage_num)
        t = info.get("type", "")
        mapping = {"EQU": "TTT", "IND": "ITT", "HMG": "Mountain", "MOG": "Road", "PAS": "Mountain", "PLN": "Flat", "VAL": "Road"}
        return mapping.get(t, t if t else "Road")

    def is_timetrial(self, stage_num):
        return self.stage_type(stage_num) in ("TTT", "ITT")

    def get_rankings(self, stage, classification=None):
        if classification is None:
            classification = "rankingTypeTrial" if self.is_timetrial(stage) else "rankingType"
        data = self._get(f"{classification}-{YEAR}-{stage}")
        if not data:
            alt = "rankingType" if classification == "rankingTypeTrial" else "rankingTypeTrial"
            data = self._get(f"{alt}-{YEAR}-{stage}")
        if not data:
            return []
        return [e for e in data if isinstance(e, dict) and "rankings" in e]

    def get_finish_rankings(self, stage, classification=None, finish_type=None):
        cps = self.get_rankings(stage, classification)
        if not cps:
            return None
        if finish_type:
            filtered = [c for c in cps if c.get("type") == finish_type]
            if filtered:
                return filtered[0]
        # Fallback: prefer stage finish (ite), then GC (itg), then highest-distance
        for preferred in ("ite", "itg"):
            matches = [c for c in cps if c.get("type") == preferred]
            if matches:
                return matches[0]
        return max(cps, key=lambda c: c.get("length", 0))

    def get_telemetry(self):
        data = self._get(f"telemetryCompetitor-{YEAR}")
        return data[0] if isinstance(data, list) and data else data

    def get_checkpoints(self, stage):
        data = self._get(f"checkpointList-{YEAR}-{stage}")
        return sorted(data, key=lambda c: c.get("length", 0)) if data else []

    def get_stage_profile(self, stage):
        data = self._get(f"checkpoint-{YEAR}-{stage}")
        if not data:
            return []
        cp_data = data[0] if isinstance(data, list) and data else data
        if not isinstance(cp_data, dict):
            return []
        results = []
        for key, cp in cp_data.items():
            if not isinstance(cp, dict):
                continue
            cp_length = cp.get("length", 0)
            for summit in cp.get("checkpointSummits", []):
                s_info = summit.get("summit", {})
                results.append({
                    "type": "climb",
                    "km": cp_length,
                    "name": s_info.get("name", ""),
                    "altitude": s_info.get("altitude", 0),
                    "length": summit.get("length", 0),
                    "code": summit.get("code", ""),
                })
            for ct in cp.get("checkpointTypes", []):
                if ct.get("type") == "chrono":
                    results.append({
                        "type": "chrono",
                        "km": cp_length,
                        "name": cp.get("place", ""),
                    })
        results.sort(key=lambda x: x["km"])
        return results


class PcsSource:
    def __init__(self):
        self._session = None

    def _get_session(self):
        if self._session is None:
            try:
                from curl_cffi import requests as cffi_requests
                self._session = cffi_requests.Session()
            except ImportError:
                return None
        return self._session

    def fetch_stage_result(self, stage, use_cache=True):
        cache_file = CACHE_DIR / f"pcs_stage_{stage}.html"
        if use_cache and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < CACHE_TTL:
                return cache_file.read_text()

        session = self._get_session()
        if session is None:
            return None

        url = f"https://www.procyclingstats.com/race/tour-de-france/{YEAR}/stage-{stage}/result"
        try:
            r = session.get(url, impersonate="chrome120", timeout=20)
            if r.status_code == 200 and "<table" in r.text:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(r.text)
                return r.text
        except Exception as e:
            print(f"PCS fetch failed: {e}", file=sys.stderr)
        return None

    def get_ttt_splits(self, stage):
        html = self.fetch_stage_result(stage)
        if not html:
            return None

        teams = []
        for table_match in re.finditer(r"<table[^>]*>(.*?)</table>", html, re.DOTALL):
            table_html = table_match.group(0)
            if 'href="rider/' not in table_html:
                continue

            rows = re.findall(r"<tr>(.*?)</tr>", table_html, re.DOTALL)
            team_name = ""
            riders = []

            for row in rows:
                team_match = re.search(r'showIfMobile[^>]*>\s*([^<]+)</div>', row)
                if team_match:
                    team_name = team_match.group(1).strip()

                rider_match = re.search(
                    r'href="rider/([^"]+)"[^>]*><span class="uppercase">(\w+)</span>\s*(\w*)</a>', row)
                gap_match = re.search(r'<font class="blue">([^<]+)</font>', row)

                if rider_match:
                    lastname = rider_match.group(2)
                    firstname = rider_match.group(3)
                    gap = gap_match.group(1) if gap_match else "winner"
                    riders.append({"firstname": firstname, "lastname": lastname, "gap": gap})

            if riders and team_name and 2 <= len(riders) <= 10:
                teams.append({"team": team_name, "riders": riders})

        return teams

    def get_speed_segments(self, stage):
        session = self._get_session()
        if session is None:
            return None

        url = f"https://www.procyclingstats.com/race/tour-de-france/{YEAR}/stage-{stage}/statistics/speed-per-segment"
        try:
            r = session.get(url, impersonate="chrome120", timeout=20)
            if r.status_code != 200:
                return None
        except Exception as e:
            print(f"PCS speed segments fetch failed: {e}", file=sys.stderr)
            return None

        html = r.text
        segments = []
        for table_match in re.finditer(r"<table[^>]*>(.*?)</table>", html, re.DOTALL):
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(0), re.DOTALL)
            for row in rows:
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
                clean = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
                if len(clean) >= 3 and "-" in clean[0] and clean[0][0].isdigit():
                    try:
                        segments.append({
                            "segment": clean[0],
                            "riders": int(clean[1]) if clean[1].isdigit() else 0,
                            "speed": float(clean[-1]),
                        })
                    except (ValueError, IndexError):
                        pass
        return segments if segments else None


class BlueskySource:
    API = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"

    def search(self, query="Tour de France", limit=20, since=None, tag=None):
        params = {"q": query, "sort": "latest", "limit": min(limit, 100)}
        if since:
            params["since"] = since
        if tag:
            params["tag"] = tag
            if f"#{tag}" not in query and tag not in query:
                params["q"] = f"{query} #{tag}"

        r = requests.get(self.API, params=params,
            headers={"Accept": "application/json"}, timeout=15)
        return r.json().get("posts", []) if r.status_code == 200 else []


class RssSource:
    FEEDS = {
        "VeloNews": "https://www.velonews.com/feed/",
        "Escape Collective": "https://escapecollective.com/feed/",
    }

    TDF_KEYWORDS = ["tour de france", "tdf", "vingegaard", "pogacar", "pogačar",
                     "barcelona", "tour", "stage 1", "yellow jersey", "maillot jaune"]

    def fetch_all(self, tdf_only=True):
        all_items = []
        for name, url in self.FEEDS.items():
            try:
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                if r.status_code != 200:
                    continue
                root = ET.fromstring(r.text)
                for item in root.findall(".//item"):
                    title = item.findtext("title", "")
                    pub = item.findtext("pubDate", "")
                    desc = item.findtext("description", "")
                    link = item.findtext("link", "")
                    clean_desc = re.sub(r"<[^>]+>", "", desc).strip()

                    text = (title + clean_desc).lower()
                    is_tdf = any(kw in text for kw in self.TDF_KEYWORDS)

                    if tdf_only and not is_tdf:
                        continue

                    clean_desc = re.sub(r'Read the full article at.*', '', clean_desc).strip()
                    all_items.append({
                        "source": name,
                        "title": title,
                        "description": clean_desc[:200],
                        "pub_date": pub,
                        "link": link,
                        "is_tdf": is_tdf,
                    })
            except Exception as e:
                print(f"RSS feed {name} parse failed: {e}", file=sys.stderr)
                continue

        all_items.sort(key=lambda x: x["pub_date"], reverse=True)
        return all_items


def fmt_time(ms):
    if ms is None or ms < 0:
        return "—"
    s = ms // 1000
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}.{ms % 1000:03d}"

def fmt_gap(ms):
    if ms == 0:
        return ""
    s = abs(ms) / 1000
    sign = "-" if ms < 0 else "+"
    if s < 60:
        return f"{sign}{s:.3f}s"
    m = int(s // 60)
    sec = int(s % 60)
    return f"{sign}{m}m{sec:02d}"

def clear_screen():
    if not os.environ.get("TERM") and os.name != "nt":
        print()
        return
    print("\033[2J\033[H", end="")

def truncate(s, n):
    return s[:n-1] + "…" if len(s) > n else s

def parse_iso_time(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def cmd_stage_result(aso, stage, top_n=0, show_cp=False, show_splits=False):
    aso.load_riders_teams()
    finish = aso.get_finish_rankings(stage)
    if not finish:
        print(f"Stage {stage} hasn't happened yet")
        return

    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    length = info.get("length", 0)
    stype = aso.stage_type(stage)
    print(f"\nStage {stage}: {dep} > {arr} ({length:.1f}km, {stype})")
    print(f"{'Pos':>4}  {'Bib':>4}  {'Name':<26} {'Team':<30} {'Time':>14} {'Gap':>10}")
    print("-" * 95)

    rankings = finish.get("rankings", [])
    limit = min(top_n, len(rankings)) if top_n else len(rankings)
    for r in rankings[:limit]:
        bib = r["bib"]
        name = aso.rider_name(bib)
        team = aso.rider_team(bib)
        time_str = fmt_time(r["absolute"])
        gap_str = fmt_gap(r["relative"])
        print(f"{r['position']:>4}  {bib:>4}  {truncate(name,26):<26} {truncate(team,30):<30} {time_str:>14} {gap_str:>10}")

    if top_n and top_n < len(rankings):
        print(f"... ({len(rankings) - top_n} more)")

    if show_cp:
        cps = aso.get_rankings(stage)
        if len(cps) > 1:
            cps.sort(key=lambda c: c.get("length", 0))
            finish_cp = max(cps, key=lambda c: c.get("length", 0))
            print("\n--- Checkpoint Splits ---")
            hdr_limit = min(10, len(finish_cp["rankings"]))
            print(f"{'CP':>4}  {'KM':>6}", end="")
            for r in finish_cp["rankings"][:hdr_limit]:
                rider = aso._riders.get(r["bib"], {})
                ln = rider.get("lastname", f"#{r['bib']}")
                print(f" {truncate(ln,14):>14}", end="")
            print()

            for cp in cps:
                print(f"CP{cp['checkpoint']:>3}  {cp.get('length',0):>6.1f}", end="")
                for hdr_r in finish_cp["rankings"][:hdr_limit]:
                    found = next((r for r in cp["rankings"] if r["bib"] == hdr_r["bib"]), None)
                    if found:
                        gap = fmt_gap(found["relative"])
                        if gap:
                            print(f" {gap:>14}", end="")
                        elif found["relative"] == 0:
                            t = fmt_time(found["absolute"])
                            print(f" {t[3:]:>14}", end="")
                        else:
                            print(f" {'0':>14}", end="")
                    else:
                        print(f" {'-':>14}", end="")
                print()

    if show_splits:
        pcs = PcsSource()
        splits = pcs.get_ttt_splits(stage)
        if splits:
            print("\n--- Individual Splits (PCS) ---")
            for team in splits:
                print(f"\n  {team['team']}")
                for r in team["riders"]:
                    gap = r["gap"]
                    marker = "  " if gap == "winner" else gap
                    print(f"    {r['firstname']:12s} {r['lastname']:20s} {marker}")
        else:
            print("\n(no individual splits - PCS couldn't be reached or not a TTT/ITT)")


def cmd_gc(aso, stage, top_n=0):
    aso.load_riders_teams()
    if stage < 1:
        stage = aso.find_latest_stage()
    finish = aso.get_finish_rankings(stage, "rankingType")
    if not finish:
        print(f"No GC data for stage {stage}")
        return
    print(f"\nGeneral Classification after Stage {stage}")
    print(f"{'Pos':>4}  {'Bib':>4}  {'Name':<26} {'Team':<30} {'Time':>14} {'Gap':>10}")
    print("-" * 95)
    rankings = finish.get("rankings", [])
    limit = min(top_n, len(rankings)) if top_n else len(rankings)
    for r in rankings[:limit]:
        bib = r["bib"]
        name = aso.rider_name(bib)
        team = aso.rider_team(bib)
        time_str = fmt_time(r["absolute"])
        gap_str = fmt_gap(r["relative"])
        print(f"{r['position']:>4}  {bib:>4}  {truncate(name,26):<26} {truncate(team,30):<30} {time_str:>14} {gap_str:>10}")


def cmd_live(aso, watch=False, interval=15):
    aso.load_riders_teams()
    while True:
        tel = aso.get_telemetry()
        if not tel:
            print("No live telemetry right now")
            return

        if watch:
            clear_screen()

        race_status = tel.get("RaceStatus", False)
        ygpw = tel.get("YGPW", [])
        raw_riders = tel.get("Riders", [])

        # --- Data clean-up: ASO API often returns duplicate entries + stale GPS ---

        # 1. Deduplicate by bib
        seen_bibs = set()
        riders = []
        for r in raw_riders:
            bib = r.get("Bib")
            if bib and bib not in seen_bibs:
                seen_bibs.add(bib)
                riders.append(r)

        # 2. Filter out impossible GPS positions against today's stage length
        stage_length = None
        today = datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y-%m-%d")
        for s in aso.load_stages():
            if s.get("date", "")[:10] == today:
                stage_length = s.get("length", 0)
                break
        if stage_length is not None and stage_length > 0:
            riders = [r for r in riders if 0 <= r.get("kmToFinish", 0) <= stage_length + 2.0]

        jersey_names = ["Yellow", "Green", "Polka", "White"]
        jersey_icons = ["🟡", "🟢", "🔴", "⚪"]

        print(f"Tour de France {YEAR} - Live")
        print(f"Status: {'IN PROGRESS' if race_status else 'Finished/Not Started'}")

        jersey_parts = []
        for i in range(4):
            if i < len(ygpw) and ygpw[i]:
                r = aso._riders.get(ygpw[i])
                if r:
                    jersey_parts.append(f"{jersey_icons[i]}{jersey_names[i][0]}={r['firstname']} {r['lastname']}")
        if jersey_parts:
            print(f"Jerseys: {'  '.join(jersey_parts)}")

        if riders:
            r0 = riders[0]
            print(f"Weather: {r0.get('degC', 0):.1f}°C, Wind {r0.get('kphWind', 0):.1f} kph")

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

        print(f"\nGroups ({len(groups)} groups, {len(riders)} riders):")
        print(f"{'Grp':>4}  {'kmToFin':>8}  {'Riders':>6}  {'kph':>6}  Key Riders")
        print("-" * 80)
        for gi, grp in enumerate(groups):
            kphs = [r.get("kph", 0) for r in grp["riders"]]
            avg_kph = sum(kphs) / len(kphs) if kphs else 0
            names = []
            for r in grp["riders"]:
                rider = aso._riders.get(r.get("Bib"))
                names.append(rider["lastname"] if rider else f"#{r.get('Bib')}")
            names_str = ", ".join(names[:8])
            if len(names) > 8:
                names_str += f" (+{len(names) - 8})"
            print(f"{gi + 1:>4}  {grp['km']:>8.2f}  {len(grp['riders']):>6}  {avg_kph:>6.1f}  {names_str}")

        if len(riders) <= 30:
            print("\nAll Riders:")
            print(f"{'Bib':>4}  {'Team':>4}  {'Name':<26} {'kph':>6} {'kmFin':>6} {'Grad%':>6} {'Wind':>5} {'Status':>8} {'Lead':>4}")
            print("-" * 85)
            for r in sorted_riders:
                bib = r.get("Bib", 0)
                name = aso.rider_name(bib)
                leader = " *" if r.get("isLeader") else ""
                print(f"{bib:>4}  {r.get('team',''):>4}  {truncate(name,26):<26} "
                      f"{r.get('kph',0):>6.1f} {r.get('kmToFinish',0):>6.2f} "
                      f"{r.get('Gradient',0):>6.1f} {r.get('kphWind',0):>5.1f} "
                      f"{r.get('Status',''):>8} {leader:>4}")

        if not watch:
            break
        print(f"\n(Refreshing every {interval}s - Ctrl+C to exit)")
        sys.stdout.flush()
        time.sleep(interval)


def cmd_where(aso, names):
    """Show live race position for specific riders by name."""
    aso.load_riders_teams()
    tel = aso.get_telemetry()
    if not tel:
        print("No live data - race probably not in progress")
        return

    raw_riders = tel.get("Riders", [])
    # Dedup + filter
    seen_bibs = set()
    riders = []
    for r in raw_riders:
        bib = r.get("Bib")
        if bib and bib not in seen_bibs:
            seen_bibs.add(bib)
            riders.append(r)
    stage_length = None
    today = datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y-%m-%d")
    for s in aso.load_stages():
        if s.get("date", "")[:10] == today:
            stage_length = s.get("length", 0)
            break
    if stage_length is not None and stage_length > 0:
        riders = [r for r in riders if 0 <= r.get("kmToFinish", 0) <= stage_length + 2.0]

    sorted_riders = sorted(riders, key=lambda r: r.get("kmToFinish", 999))
    leader_km = sorted_riders[0].get("kmToFinish", 0) if sorted_riders else 0

    # Search rider database for matching names
    matches = []
    for name in names:
        name_lower = name.lower().replace(" ", "")
        found = False
        for bib, info in aso._riders.items():
            full = f"{info['firstname']}{info['lastname']}".lower().replace(" ", "")
            if name_lower in full:
                matches.append((bib, info, name))
                found = True
        if not found:
            print(f"  '{name}' not found")
    if not matches:
        print("No riders matched. Try 'Pogacar' or 'Vingegaard'.")
        return

    # Map bib to telemetry
    tel_by_bib = {r.get("Bib"): r for r in riders}

    print(f"{'Bib':>4}  {'Name':<24} {'Team':<22} {'kmToFin':>8} {'Gap':>6} {'Speed':>6} {'Grad%':>5} {'Status':>8}")
    print("-" * 90)
    for bib, info, query in matches:
        entry = tel_by_bib.get(bib)
        if entry:
            km = entry.get("kmToFinish", 0)
            gap = km - leader_km
            print(f"{bib:>4}  {info['firstname'] + ' ' + info['lastname']:<24} "
                  f"{aso._teams.get(info['team_code'], {}).get('name', info['team_code']):<22} "
                  f"{km:>8.2f} {gap:>+6.2f} "
                  f"{entry.get('kph', 0):>6.1f} {entry.get('Gradient', 0):>5.1f} "
                  f"{entry.get('Status', 'unknown'):>8}")
        else:
            print(f"{bib:>4}  {info['firstname'] + ' ' + info['lastname']:<24} "
                  f"{aso._teams.get(info['team_code'], {}).get('name', info['team_code']):<22} "
                  f"{'NOT TRACKED':>8} {'':>6} {'':>6} {'':>5} {'no GPS':>8}")
    if sorted_riders:
        print(f"\nLeader: {aso._riders.get(sorted_riders[0].get('Bib'), {}).get('lastname', '?')} "
              f"at {leader_km:.2f}km to finish")


def cmd_jerseys(aso, stage=0):
    aso.load_riders_teams()
    tel = aso.get_telemetry()
    if not tel:
        print("No jersey data available")
        return

    ygpw = tel.get("YGPW", [])
    jersey_names = ["YELLOW (GC)", "GREEN (Points)", "POLKA DOT (KOM)", "WHITE (U25)"]
    jersey_icons = ["🟡", "🟢", "🔴", "⚪"]

    print(f"Tour de France {YEAR} - Jerseys")
    if stage > 0:
        print(f"(after Stage {stage})")
    print("(live telemetry - shows current leader mid-race, not final GC)")
    print()

    for i in range(4):
        if i < len(ygpw) and ygpw[i]:
            bib = ygpw[i]
            r = aso._riders.get(bib)
            if r:
                print(f"{jersey_icons[i]} {jersey_names[i]:<18} {r['firstname']} {r['lastname']}  "
                      f"({r['team_name']}, bib {bib})")
            else:
                print(f"{jersey_icons[i]} {jersey_names[i]:<18} Bib #{bib}")


def cmd_bsky(aso, query="Tour de France", watch=False, interval=30, tag=None):
    bs = BlueskySource()
    while True:
        if watch:
            clear_screen()
        since = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        posts = bs.search(query=query, limit=25, since=since, tag=tag)

        print(f"Tour de France {YEAR} - Bluesky")
        print(f"Search: '{query}'" + (f" #{tag}" if tag else ""))
        print(f"{'Time (UTC)':>12}  {'Author':<30}  Post")
        print("-" * 100)

        for p in posts:
            record = p.get("record", {})
            author = p.get("author", {}).get("handle", "?")
            text = record.get("text", "").replace("\n", " ")
            created = record.get("createdAt", "")
            dt = parse_iso_time(created)
            time_str = dt.strftime("%H:%M:%S") if dt else created[:8]
            print(f"{time_str:>12}  {truncate(author,30):<30}  {truncate(text,80)}")

        if not posts:
            print("  (nothing found)")

        if not watch:
            break
        print(f"\n(Refreshing every {interval}s - Ctrl+C to exit)")
        sys.stdout.flush()
        time.sleep(interval)


def cmd_news(aso, watch=False, interval=60):
    rss = RssSource()
    while True:
        if watch:
            clear_screen()
        items = rss.fetch_all(tdf_only=True)

        print(f"Tour de France {YEAR} - News")
        print(f"{'Source':<20}  {'Published':>26}  Title")
        print("-" * 110)

        for item in items[:20]:
            print(f"{item['source']:<20}  {item['pub_date'][:26]:>26}  {truncate(item['title'],70)}")
            if item["description"]:
                print(f"{'':>20}  {'':>26}  {truncate(item['description'],80)}")

        if not items:
            print("  (no TDF articles right now)")

        if not watch:
            break
        print(f"\n(Refreshing every {interval}s - Ctrl+C to exit)")
        sys.stdout.flush()
        time.sleep(interval)


def cmd_stages(aso):
    stages = aso.load_stages()
    print(f"Tour de France {YEAR} - {len(stages)} Stages")
    print(f"{'Stg':>4}  {'Date':<12}  {'From':<30} {'To':<30} {'KM':>6}  {'Type':<10}")
    print("-" * 100)
    for s in stages:
        stage = s.get("stage", 0)
        date = s.get("date", "")[:10]
        dep = s.get("departureCity", {}).get("label", "?")
        arr = s.get("arrivalCity", {}).get("label", "?")
        length = s.get("length", 0)
        stype = aso.stage_type(stage)
        print(f"{stage:>4}  {date:<12}  {truncate(dep,30):<30} {truncate(arr,30):<30} {length:>6.0f}  {stype:<10}")


def cmd_teams(aso):
    _, teams = aso.load_riders_teams()
    team_list = sorted(teams.values(), key=lambda t: t.get("name", ""))
    print(f"Tour de France {YEAR} - Teams ({len(team_list)})")
    print(f"{'#':>3}  {'Team Name':<40}  {'Code':<6}  {'Country':<10}")
    print("-" * 65)
    for i, t in enumerate(team_list, 1):
        print(f"{i:>3}  {truncate(t.get('name',''),40):<40}  {t.get('code',''):<6}  {t.get('nationality',''):<10}")


def cmd_riders(aso, top_n=0):
    riders, _ = aso.load_riders_teams()
    rider_list = sorted(riders.values(), key=lambda r: r["bib"])
    print(f"Tour de France {YEAR} - Riders ({len(rider_list)})")
    print(f"{'Bib':>4}  {'Name':<26}  {'Nat':>4}  {'Code':<6}  {'Team':<40}")
    print("-" * 85)
    limit = min(top_n, len(rider_list)) if top_n else len(rider_list)
    for r in rider_list[:limit]:
        name = f"{r['firstname']} {r['lastname']}".strip()
        print(f"{r['bib']:>4}  {truncate(name,26):<26}  {r['nationality']:>4}  "
              f"{r.get('team_code',''):<6}  {truncate(r.get('team_name',''),40):<40}")


def cmd_checkpoints(aso, stage):
    cps = aso.get_checkpoints(stage)
    if not cps:
        print(f"No checkpoints for stage {stage}")
        return
    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    print(f"Stage {stage}: {dep} > {arr} - Checkpoints ({len(cps)})")
    print(f"{'CP':>4}  {'KM':>7}  {'Type':<8}  {'Road/Location':<35}  {'Place':<20}  {'Schedule':<12}  {'Climb'}")
    print("-" * 120)

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
        print(f"{cp_num:>4}  {length:>7.1f}  {type_str:<8}  {truncate(road,35):<35}  "
              f"{truncate(place,20):<20}  {sched:<12}  {climb}")


def cmd_profile(aso, stage):
    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    length = info.get("length", 0)
    print(f"Stage {stage}: {dep} > {arr} ({length:.1f}km) - Profile\n")

    profile = aso.get_stage_profile(stage)
    if not profile:
        print("  No profile data for this stage")
        return

    cat_map = {"H": "HC", "1": "Cat 1", "2": "Cat 2", "3": "Cat 3", "4": "Cat 4", "X": "Climb"}
    n_climbs = 0
    for entry in profile:
        if entry["type"] == "climb":
            cat = cat_map.get(entry.get("code", ""), "Climb")
            print(f"  {cat:<6} at km {entry['km']:<6.1f}  {entry['name']:<40}  "
                  f"{entry['altitude']:>4.0f}m  length: {entry['length']:.0f}m")
            n_climbs += 1
        elif entry["type"] == "chrono":
            print(f"  {'CHRONO':<6} at km {entry['km']:<6.1f}  {entry['name']}")

    if n_climbs == 0:
        print("  No categorised climbs on this stage")


def cmd_speed(aso, stage):
    pcs = PcsSource()
    segments = pcs.get_speed_segments(stage)
    if not segments:
        print(f"No speed data for stage {stage}")
        return
    info = aso.stage_info(stage)
    dep = info.get("departureCity", {}).get("label", "?")
    arr = info.get("arrivalCity", {}).get("label", "?")
    print(f"Stage {stage}: {dep} > {arr} - Speed per Segment\n")
    print(f"{'Segment (km)':>15}  {'Riders':>7}  {'Avg Speed':>10}")
    print("-" * 40)
    for seg in segments:
        print(f"{seg['segment']:>15}  {seg['riders']:>7}  {seg['speed']:>9.1f} kph")


def main():
    parser = argparse.ArgumentParser(
        prog="tdf",
        description=f"Tour de France {YEAR} - results, live GPS, Bluesky narrative, PCS splits",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Results:
  tdf                      Latest stage results
  tdf 1                    Stage 1 results
  tdf 1 --top 10           Top 10 for stage 1
  tdf 1 --splits           Individual TTT/ITT splits (from PCS)
  tdf 1 --cp               Checkpoint splits
  tdf --gc 1 --top 5       General classification after stage 1

Live:
  tdf --live               Live race state (GPS, groups, speeds)
  tdf --live --watch       Auto-refresh every 15s
  tdf --jerseys            Current jersey holders

Narrative:
  tdf --bsky               Bluesky posts about the Tour
  tdf --bsky "Vauquelin"   Search Bluesky for a topic
  tdf --bsky --tag TDF2026 Search by hashtag
  tdf --bsky --watch 30    Auto-refresh every 30s
  tdf --news               News articles (RSS feeds)
  tdf --news --watch 60    Auto-refresh every 60s

Info:
  tdf --stages             All 21 stages
  tdf --teams              All teams
  tdf --riders             All 184 riders
  tdf --checkpoints 1      Checkpoint locations for stage 1
  tdf --profile 6          Stage 6 climb profile
  tdf --speed 1            Average speed per segment (PCS)""")

    parser.add_argument("stage", type=int, nargs="?", default=-1, help="Stage number (1-21)")
    parser.add_argument("--top", type=int, default=0, metavar="N", help="Top N riders")
    parser.add_argument("--splits", action="store_true", help="Individual TTT/ITT splits (PCS)")
    parser.add_argument("--cp", action="store_true", help="Checkpoint splits")
    parser.add_argument("--speed", action="store_true", help="Speed per segment (PCS)")
    parser.add_argument("--gc", action="store_true", help="General classification")
    parser.add_argument("--live", action="store_true", help="Live race state")
    parser.add_argument("--jerseys", action="store_true", help="Current jersey holders")
    parser.add_argument("--stages", action="store_true", help="List all stages")
    parser.add_argument("--teams", action="store_true", help="List all teams")
    parser.add_argument("--riders", action="store_true", help="List all riders")
    parser.add_argument("--where", nargs="+", default=None, metavar="NAME",
                        help="Track specific rider positions (e.g. --where Pogacar Vingegaard)")
    parser.add_argument("--checkpoints", action="store_true", help="Checkpoint locations")
    parser.add_argument("--profile", action="store_true", help="Stage climb profile")
    parser.add_argument("--bsky", nargs="?", const="Tour de France", default=None,
                        metavar="QUERY", help="Bluesky social feed")
    parser.add_argument("--tag", default=None, metavar="TAG", help="Bluesky hashtag (no #)")
    parser.add_argument("--news", action="store_true", help="RSS news feed")
    parser.add_argument("--watch", nargs="?", const=15, type=int, default=0,
                        metavar="SEC", help="Auto-refresh (default: 15s)")
    parser.add_argument("--version", action="version", version=f"tdf {YEAR} (Python)")

    args = parser.parse_args()
    aso = AsoSource()
    stage = args.stage if args.stage > 0 else -1

    if args.stage == 0:
        parser.error("stage number must be between 1 and 21")

    if stage < 0:
        try:
            stage = aso.find_latest_stage()
        except Exception as e:
            print(f"find_latest_stage failed: {e}", file=sys.stderr)
            stage = 1

    watch = args.watch > 0
    interval = args.watch if args.watch > 0 else 15

    if args.live:
        cmd_live(aso, watch=watch, interval=interval)
    elif args.jerseys:
        cmd_jerseys(aso, stage=stage)
    elif args.bsky is not None:
        cmd_bsky(aso, query=args.bsky, watch=watch, interval=interval, tag=args.tag)
    elif args.news:
        cmd_news(aso, watch=watch, interval=interval)
    elif args.stages:
        cmd_stages(aso)
    elif args.teams:
        cmd_teams(aso)
    elif args.riders:
        cmd_riders(aso, top_n=args.top)
    elif args.where is not None:
        cmd_where(aso, args.where)
    elif args.checkpoints:
        cmd_checkpoints(aso, stage)
    elif args.profile:
        cmd_profile(aso, stage)
    elif args.speed:
        cmd_speed(aso, stage)
    elif args.gc:
        cmd_gc(aso, stage, top_n=args.top)
    else:
        cmd_stage_result(aso, stage, top_n=args.top, show_cp=args.cp, show_splits=args.splits)


if __name__ == "__main__":
    main()
