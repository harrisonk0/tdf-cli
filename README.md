# TDF CLI 🚴

**Tour de France 2026 from your terminal** — live results, GPS tracking, race narrative, and individual rider splits, all in one CLI tool.

```
tdf                          # Latest stage results
tdf --live --watch           # Live race state (GPS, speeds, groups)
tdf --bsky "Vauquelin"       # Bluesky narrative about a rider
tdf 1 --splits               # Individual TTT splits for every rider
```

## Features

| What | How |
|------|-----|
| **Stage results** | Full GC for all 184 riders with times + gaps |
| **Live GPS tracking** | Real-time rider positions, speeds, groups, weather |
| **Individual TTT splits** | Every rider's time within their team (PCS via `curl_cffi`) |
| **Jersey holders** | 🟡 Yellow, 🟢 Green, 🔴 Polka, ⚪ White |
| **Narrative feed** | Bluesky posts about the race — punctures, crashes, tactics in real time |
| **News articles** | RSS from VeloNews + Escape Collective |
| **Checkpoint splits** | Intermediate timings for any stage |
| **Climb profiles** | Categorised climbs with altitude, length, gradient |
| **Speed per segment** | Average speed broken down by section of each stage |
| **Auto-refresh** | `--watch` flag for live dashboard |

## Quick Start

```bash
# Requires Python 3.9+ and pip
pip install requests curl_cffi

# Download the script
curl -o /usr/local/bin/tdf https://raw.githubusercontent.com/harrisonk0/tdf-cli/main/tdf.py
chmod +x /usr/local/bin/tdf

# Start using it
tdf                     # Latest stage
tdf --help              # Full command reference
```

## Usage

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

Tour de France 2026 - Bluesky Live Feed
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
| [ProCyclingStats](https://procyclingstats.com) | Individual TTT splits, speed segments | Bypasses Cloudflare (TLS fingerprint) |
| [Bluesky API](https://docs.bsky.app) | Live social narrative | Free, no key |
| [VeloNews](https://velo.outsideonline.com) + [Escape Collective](https://escapecollective.com) | News articles | Free RSS feeds |

## Requirements

- Python 3.9+
- `requests` (ASO API, Bluesky, RSS)
- `curl_cffi` (PCS - TLS-level Cloudflare bypass)

## Development

```bash
git clone https://github.com/harrisonk0/tdf-cli.git
cd tdf-cli
python3 tdf.py --help
```

The entire tool is a single Python file (`tdf.py`) with clear class structure — easy to extend.

## License

MIT — do what you want with it.
