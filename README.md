# TDF CLI

**Tour de France 2026 from your terminal** - live results, GPS tracking, race narrative, individual rider splits.

```bash
tdf                          # Latest stage results
tdf --live --watch           # Live race state (GPS, speeds, groups)
tdf --bsky "Vauquelin"       # Bluesky narrative about a rider
tdf 1 --splits               # Individual TTT splits for every rider
```

---

## Quick start (hosted MCP)

Wire this into any MCP client (Claude Desktop, Cline, Cursor, Hermes, whatever):

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

No Python, no install - just the URL. First call after ~15 min idle takes ~10s while Render spins up, then it's instant.

Available tools:

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

Also has a `tdf://stages` resource that lists all 21 stages.

---

## Self-host your own MCP/CLI

### MCP (local stdio)

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

### MCP (your own SSE server)

```bash
python3 tdf_mcp.py --transport sse --host 0.0.0.0 --port 8000
```

Point your agent at `http://your-host:8000/sse`.

### CLI

```bash
pip install requests curl_cffi
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

---

## Example

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

---

## Data Sources

| Source | Data | Auth |
|--------|------|------|
| [ASO Racecenter API](https://racecenter.letour.fr) | Official times, GPS telemetry, stages, riders, teams | Free, no key |
| [ProCyclingStats](https://procyclingstats.com) | Individual TTT splits, speed segments | Cloudflare bypass (TLS fingerprint) |
| [Bluesky API](https://docs.bsky.app) | Live social narrative | Free, no key |
| [VeloNews](https://velo.outsideonline.com) + [Escape Collective](https://escapecollective.com) | News articles | Free RSS feeds |

## License

MIT - do what you want with it.
