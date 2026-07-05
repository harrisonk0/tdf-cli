# TDF CLI

**Tour de France 2026 from your terminal** - live results, GPS tracking, race narrative, individual rider splits.

```bash
tdf                          # Latest stage results
tdf --live --watch           # Live race state (GPS, speeds, groups)
tdf --bsky "Vauquelin"       # Bluesky narrative about a rider
tdf 1 --splits               # Individual TTT splits for every rider
```

---

## MCP Server

Built-in MCP server so Claude Desktop, Cline, Cursor, Hermes, Continue, or Windsurf can query Tour data directly.

| Tool | Does |
|------|------|
| `get_stage_result` | Stage results with times and gaps |
| `get_gc` | General classification (all 184 riders) |
| `get_jerseys` | Yellow, Green, Polka Dot, White holders |
| `get_live_state` | Real-time GPS, groups, speeds, weather |
| `get_bluesky_feed` | Race narrative - punctures, crashes, tactics |
| `get_news` | RSS articles from cycling journalism |
| `get_stage_profile` | Climb categories, altitude, length |
| `get_ttt_splits` | Per-rider TTT splits |
| `get_speed_segments` | Average speed per stage segment |
| `get_checkpoints` | Checkpoint locations with schedules |
| `get_stage_checkpoint_splits` | Checkpoint timing gaps |
| `get_teams` | All 23 teams with codes |
| `search_riders` | Rider lookup by name |

Also a `tdf://stages` resource listing all 21 stages.

### Option A: hosted (no install)

Connect your MCP client to the public endpoint:

```json
{
  "mcpServers": {
    "tdf": {
      "url": "https://tdf-mcp.onrender.com/sse",
      "timeout": 30
    }
  }
}
```

No Python, no install — just add the URL. First call after idle might take ~10s while Render wakes up — after that it's instant.

### Option B: local (self-host)

Requirements: Python 3.9+, `pip install requests curl_cffi mcp`.

**Claude Desktop / Cline / Cursor:**

```json
{
  "mcpServers": {
    "tdf": {
      "command": "python3",
      "args": ["/path/to/tdf_mcp.py"],
      "timeout": 30
    }
  }
}
```

**Hermes Agent:**

```bash
hermes config set mcp_servers '{"tdf":{"command":"python3","args":["/path/to/tdf_mcp.py"],"timeout":30}}'
systemctl --user restart hermes-gateway.service
```

**Run your own SSE server:**

```bash
python3 tdf_mcp.py --transport sse --host 0.0.0.0 --port 8000
```

Then configure your agent to connect to `http://your-host:8000/sse`.

---

## CLI Usage

### Results

```bash
tdf                      # Latest stage results
tdf 3                    # Stage 3 results
tdf 1 --top 10           # Top 10 for stage 1
tdf 1 --cp               # Checkpoint splits
tdf 1 --splits           # Individual TTT/ITT splits (per team)
tdf --gc 1 --top 5       # General classification after stage 1
```

### Live

```bash
tdf --live               # Current race state (GPS, groups, speeds)
tdf --live --watch       # Auto-refresh every 15s
tdf --jerseys            # Current jersey holders
```

### Narrative

```bash
tdf --bsky               # Latest Bluesky posts about the Tour
tdf --bsky "Vauquelin"   # Search for a specific rider/topic
tdf --bsky --tag TDF2026 # Filter by hashtag
tdf --news               # RSS news feed
```

### Info

```bash
tdf --stages             # All 21 stages
tdf --teams              # All 23 teams
tdf --riders             # All 184 riders
tdf --checkpoints 6      # Checkpoint locations for stage 6
tdf --profile 19         # Stage 19 climb profile (Alpe d'Huez)
tdf --speed 1            # Average speed per segment
```

## Example Output

```
$ tdf 1 --top 5

Stage 1: Barcelone > Barcelone (19.6km, TTT)
 Pos   Bib  Name                       Time        Gap
   1    11  Jonas VINGEGAARD HANSEN    00:21:47.870
   2    81  Egan BERNAL GOMEZ          00:21:55.200    +8.000s
   3     1  Tadej POGACAR              00:21:59.150   +12.000s
   4    31  Juan AYUSO PESQUERA        00:22:03.140   +16.000s
   5    21  Remco EVENEPOEL            00:22:06.020   +19.000s
```

```
$ tdf --bsky "Vauquelin puncture"

Tour de France 2026 - Bluesky
  Time (UTC)  Author                          Post
  ----------------------------------------------------------------------------
    16:31:35  coureur.app                     Was that a puncture for Vauquelin? 😲
    16:32:31  djcaress.bsky.social            Looks like I jinxed Vauquelin, absolutely gutted for him
    16:38:27  simiscyclist.bsky.social        I think they had them with Vauquelin but the puncture messed everything up
    17:11:47  tntsports.zpravobot             Kevin Vauquelin suffered a puncture... yellow jersey hopes vanished
```

## Data Sources

| Source | Data | Auth |
|--------|------|------|
| [ASO Racecenter API](https://racecenter.letour.fr) | Official times, GPS telemetry, stages, riders, teams | Free, no key |
| [ProCyclingStats](https://procyclingstats.com) | Individual TTT splits, speed segments | Cloudflare bypass (TLS fingerprint) |
| [Bluesky API](https://docs.bsky.app) | Live social narrative | Free, no key |
| [VeloNews](https://velo.outsideonline.com) + [Escape Collective](https://escapecollective.com) | News articles | Free RSS feeds |

## Requirements

Python 3.9+, `requests`, `curl_cffi`.

```bash
pip install requests curl_cffi
```

## Install

```bash
curl -o /usr/local/bin/tdf https://raw.githubusercontent.com/harrisonk0/tdf-cli/main/tdf.py
chmod +x /usr/local/bin/tdf
tdf --help
```

Or clone the repo:

```bash
git clone https://github.com/harrisonk0/tdf-cli.git
cd tdf-cli
python3 tdf.py --help
```

## License

MIT - do what you want with it.
