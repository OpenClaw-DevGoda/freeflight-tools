# freeflight-tools

Code tools to help free flight competitors — five Python modules covering weather, airspace compliance, GPS analysis, thermal prediction, and competition management.

---

## Modules

### 1. `weather_forecast.py` — Real-time Weather Forecasting

Aggregates aviation weather feeds (METAR via aviationweather.gov) for a planned route and returns a flight-relevant forecast.

**Inputs**
- `--waypoints` — semicolon-separated `lat,lon` pairs
- `--altitude` — altitude in feet (default 5000)
- `--window` — ISO-8601 time window (optional)

**Outputs** — JSON with wind speed/direction, turbulence index, precipitation probability, and weather alerts per waypoint.

```bash
python weather_forecast.py \
  --waypoints "34.05,-118.25;34.10,-118.30" \
  --altitude 5000 \
  --window "2026-04-19T14:00/PT3H"
```

---

### 2. `airspace_checker.py` — Airspace & NOTAM Compliance

Cross-references a planned route against restricted zones (circles/polygons) and live NOTAMs. Returns a compliance score and rerouting suggestions.

**Inputs**
- `--waypoints` — semicolon-separated `lat,lon,alt_ft` triples
- `--depart` — departure time ISO-8601
- `--arrive` — arrival time ISO-8601

**Outputs** — JSON with `compliance_score` (0–100), list of conflicts, and per-waypoint rerouting suggestions.

```bash
python airspace_checker.py \
  --waypoints "34.05,-118.25,5000;34.10,-118.30,5500" \
  --depart "2026-04-19T14:00:00Z" \
  --arrive "2026-04-19T17:00:00Z"
```

---

### 3. `gps_analyzer.py` — GPS Track Analysis

Parses `.gpx` or `.tcx` files and computes competition-ready performance metrics.

**Inputs**
- `--file` — path to `.gpx` or `.tcx` track file
- `--csv` — (optional) path for CSV summary export

**Outputs** — JSON with `total_distance_km`, `max_speed_kmh`, `avg_ascent_rate_ms`, `thermal_count`, `duration_s`, and detected thermal locations.

```bash
python gps_analyzer.py --file flight.gpx
python gps_analyzer.py --file flight.gpx --csv summary.csv
```

---

### 4. `thermal_mapper.py` — Thermal Mapping & Prediction

Builds a heat-map overlay of predicted thermal zones using terrain elevation, surface temperature, and wind shear.

**Inputs**
- `--lat`, `--lon` — centre of area of interest
- `--radius` — search radius in km (default 20)
- `--wind-shear` — wind shear in knots (default 8)
- `--surface-temp` — surface temperature in °C (default 22)

**Outputs** — JSON heat-map grid with per-point `thermal_score` [0–1] and `top_zones` list of the 5 best predicted thermal sites.

```bash
python thermal_mapper.py \
  --lat 34.05 --lon -118.25 \
  --radius 30 \
  --wind-shear 8 \
  --surface-temp 28
```

---

### 5. `competition_manager.py` — Competition Management & Live Leaderboards

Scores pilot flights, ranks competitors, and can serve a live leaderboard over HTTP.

**Subcommands**

| Subcommand | Purpose |
|---|---|
| `score` | Score a single pilot's flight |
| `serve` | Run a live leaderboard HTTP server |
| `export` | Export a leaderboard snapshot to JSON |

**Rules file** (`rules.json`, optional) — override scoring weights:
```json
{
  "task_name": "Free Distance",
  "scoring": {
    "distance_weight": 1.0,
    "speed_weight": 0.5,
    "speed_threshold_kmh": 30,
    "thermal_bonus": 5.0,
    "max_score": 1000
  }
}
```

```bash
# Score one pilot
python competition_manager.py score \
  --rules rules.json \
  --pilot Alice \
  --gpx alice_flight.gpx

# Live leaderboard on port 8080
python competition_manager.py serve \
  --rules rules.json \
  --participants participants.json \
  --logs-dir ./flight_logs \
  --port 8080

# Export snapshot
python competition_manager.py export \
  --rules rules.json \
  --participants participants.json \
  --logs-dir ./flight_logs \
  --output results.json
```

`participants.json` is a simple JSON array of pilot names:
```json
["Alice", "Bob", "Charlie"]
```

---

## Requirements

All modules use the Python standard library only (`urllib`, `xml`, `http.server`, `csv`, `json`, `math`).  
No third-party packages required. Python 3.10+ recommended.

---

## Project Background

freeflight-tools is an open-source collection of Python utilities for free-flight competitors — paraglider and hang-glider pilots who navigate cross-country routes using thermals and weather judgment. The tools cover the full competition workflow: pre-flight planning (weather, airspace), in-flight data capture (GPS tracks), post-flight analysis (thermals, performance), and event management (scoring, leaderboards).

Built with AI assistance as part of an open-source initiative to give free-flight competitors practical, dependency-free tools they can run anywhere Python is available.
