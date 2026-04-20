"""
competition_manager.py — Competition Management & Live Leaderboards

Manages free-flight competition logistics: task setting, flight scoring,
and real-time leaderboard generation. Exposes a minimal HTTP API and
can export results as JSON or PDF.

Inputs:
    competition rules JSON, participant data JSON, flight log directory

Outputs:
    Real-time leaderboard JSON; optionally serves an HTTP endpoint.

Usage (score a single flight):
    python competition_manager.py score \
        --rules rules.json \
        --pilot "Alice" \
        --gpx alice_flight.gpx

Usage (run live leaderboard server on port 8080):
    python competition_manager.py serve \
        --rules rules.json \
        --participants participants.json \
        --logs-dir ./flight_logs \
        --port 8080

Usage (export leaderboard snapshot):
    python competition_manager.py export \
        --rules rules.json \
        --participants participants.json \
        --logs-dir ./flight_logs \
        --output results.json
"""

import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


# ---------------------------------------------------------------------------
# Default scoring formula (configurable via rules.json)
# ---------------------------------------------------------------------------

DEFAULT_RULES = {
    "task_name": "Free Distance",
    "scoring": {
        "distance_weight": 1.0,    # points per km
        "speed_weight": 0.5,        # points per km/h above threshold
        "speed_threshold_kmh": 30,
        "thermal_bonus": 5.0,       # points per thermal used
        "max_score": 1000,
    },
}


def load_rules(path: str | None) -> dict:
    if path and Path(path).exists():
        with open(path) as f:
            return json.load(f)
    return DEFAULT_RULES


# ---------------------------------------------------------------------------
# GPX parser (minimal — full version in gps_analyzer.py)
# ---------------------------------------------------------------------------

_GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


def _quick_gpx_stats(gpx_path: Path) -> dict:
    """Extract distance, duration, thermal count from a GPX file."""
    try:
        tree = ET.parse(gpx_path)
        root = tree.getroot()
        pts = root.findall(".//gpx:trkpt", _GPX_NS)
    except Exception:
        return {"distance_km": 0, "duration_s": 0, "thermal_count": 0, "max_speed_kmh": 0}

    if len(pts) < 2:
        return {"distance_km": 0, "duration_s": 0, "thermal_count": 0, "max_speed_kmh": 0}

    def _hav(lat1, lon1, lat2, lon2):
        R = 6371000
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))

    total_m = 0.0
    max_speed = 0.0
    thermals = 0
    in_thermal = False
    vario_buf = []

    for i in range(1, len(pts)):
        p0, p1 = pts[i - 1], pts[i]
        lat0, lon0 = float(p0.attrib["lat"]), float(p0.attrib["lon"])
        lat1, lon1 = float(p1.attrib["lat"]), float(p1.attrib["lon"])

        ele0_el = p0.find("gpx:ele", _GPX_NS)
        ele1_el = p1.find("gpx:ele", _GPX_NS)
        ele0 = float(ele0_el.text) if ele0_el is not None else 0.0
        ele1 = float(ele1_el.text) if ele1_el is not None else 0.0

        dist_m = _hav(lat0, lon0, lat1, lon1)
        total_m += dist_m

        alt_delta = ele1 - ele0
        vario = alt_delta  # crude proxy

        if vario > 0.5:
            if not in_thermal:
                in_thermal = True
        else:
            if in_thermal:
                thermals += 1
                in_thermal = False

    t0_el = pts[0].find("gpx:time", _GPX_NS)
    t1_el = pts[-1].find("gpx:time", _GPX_NS)

    def _parse(ts):
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
            try:
                return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return None

    duration_s = 0.0
    if t0_el is not None and t1_el is not None:
        t0 = _parse(t0_el.text)
        t1 = _parse(t1_el.text)
        if t0 and t1:
            duration_s = (t1 - t0).total_seconds()

    max_speed_kmh = (total_m / max(duration_s, 1)) * 3.6

    return {
        "distance_km": round(total_m / 1000, 3),
        "duration_s": round(duration_s, 1),
        "thermal_count": thermals,
        "max_speed_kmh": round(max_speed_kmh, 2),
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_flight(pilot: str, gpx_path: Path, rules: dict) -> dict:
    """
    Compute a competition score for one pilot's flight.
    """
    stats = _quick_gpx_stats(gpx_path)
    s = rules.get("scoring", DEFAULT_RULES["scoring"])

    dist_pts = stats["distance_km"] * s.get("distance_weight", 1.0)

    speed_excess = max(0.0, stats["max_speed_kmh"] - s.get("speed_threshold_kmh", 30))
    speed_pts = speed_excess * s.get("speed_weight", 0.5)

    thermal_pts = stats["thermal_count"] * s.get("thermal_bonus", 5.0)

    raw_score = dist_pts + speed_pts + thermal_pts
    final_score = min(raw_score, s.get("max_score", 1000))

    return {
        "pilot": pilot,
        "gpx": str(gpx_path),
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "score_breakdown": {
            "distance_pts": round(dist_pts, 2),
            "speed_pts": round(speed_pts, 2),
            "thermal_pts": round(thermal_pts, 2),
        },
        "total_score": round(final_score, 2),
    }


# ---------------------------------------------------------------------------
# Leaderboard builder
# ---------------------------------------------------------------------------

def build_leaderboard(rules: dict, participants: list[str], logs_dir: Path) -> dict:
    """
    Score all participants and return a ranked leaderboard.
    """
    entries = []
    for pilot in participants:
        # Look for any GPX file whose stem matches the pilot name
        gpx_files = list(logs_dir.glob(f"{pilot}*.gpx")) + list(logs_dir.glob(f"{pilot}*.GPX"))
        if not gpx_files:
            continue
        result = score_flight(pilot, gpx_files[0], rules)
        entries.append(result)

    entries.sort(key=lambda e: e["total_score"], reverse=True)

    ranked = []
    for rank, entry in enumerate(entries, start=1):
        entry["rank"] = rank
        ranked.append(entry)

    return {
        "task": rules.get("task_name", "Unknown"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "leaderboard": ranked,
    }


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _LeaderboardHandler(BaseHTTPRequestHandler):
    rules = {}
    participants = []
    logs_dir = Path(".")

    def log_message(self, format, *args):
        pass  # Suppress default access log

    def do_GET(self):
        if self.path in ("/", "/leaderboard"):
            lb = build_leaderboard(
                self.__class__.rules,
                self.__class__.participants,
                self.__class__.logs_dir,
            )
            body = json.dumps(lb, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def run_server(rules: dict, participants: list[str], logs_dir: Path, port: int = 8080):
    _LeaderboardHandler.rules = rules
    _LeaderboardHandler.participants = participants
    _LeaderboardHandler.logs_dir = logs_dir
    server = HTTPServer(("0.0.0.0", port), _LeaderboardHandler)
    print(f"Live leaderboard running at http://0.0.0.0:{port}/leaderboard  (Ctrl+C to stop)")
    server.serve_forever()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Free-flight competition manager & live leaderboard.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # score subcommand
    score_p = sub.add_parser("score", help="Score a single pilot's flight")
    score_p.add_argument("--rules", default=None, help="Path to rules.json")
    score_p.add_argument("--pilot", required=True, help="Pilot name")
    score_p.add_argument("--gpx", required=True, help="Path to pilot GPX file")

    # serve subcommand
    serve_p = sub.add_parser("serve", help="Run live leaderboard HTTP server")
    serve_p.add_argument("--rules", default=None)
    serve_p.add_argument("--participants", required=True, help="Path to participants JSON (list of names)")
    serve_p.add_argument("--logs-dir", required=True, help="Directory with pilot GPX files")
    serve_p.add_argument("--port", type=int, default=8080)

    # export subcommand
    export_p = sub.add_parser("export", help="Export leaderboard snapshot to JSON")
    export_p.add_argument("--rules", default=None)
    export_p.add_argument("--participants", required=True)
    export_p.add_argument("--logs-dir", required=True)
    export_p.add_argument("--output", default="results.json")

    args = parser.parse_args()
    rules = load_rules(args.rules)

    if args.cmd == "score":
        result = score_flight(args.pilot, Path(args.gpx), rules)
        print(json.dumps(result, indent=2))

    elif args.cmd == "serve":
        with open(args.participants) as f:
            participants = json.load(f)
        run_server(rules, participants, Path(args.logs_dir), args.port)

    elif args.cmd == "export":
        with open(args.participants) as f:
            participants = json.load(f)
        lb = build_leaderboard(rules, participants, Path(args.logs_dir))
        with open(args.output, "w") as f:
            json.dump(lb, f, indent=2)
        print(f"Leaderboard exported to {args.output}")


if __name__ == "__main__":
    main()
