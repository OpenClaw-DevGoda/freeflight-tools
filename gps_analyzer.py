"""
gps_analyzer.py — GPS Track Analysis & Performance Metrics

Parses GPX or TCX files and computes competition-relevant performance metrics:
distance, max speed, average ascent rate, descent rate, thermal count, and more.

Inputs:
    GPS track file (.gpx or .tcx)

Outputs:
    JSON summary with metrics and optional CSV export.

Usage:
    python gps_analyzer.py --file flight.gpx
    python gps_analyzer.py --file flight.gpx --csv output.csv
"""

import argparse
import csv
import json
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

_GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}
_TCX_NS = {"tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}


def _parse_gpx(path: Path) -> list[dict]:
    tree = ET.parse(path)
    root = tree.getroot()
    points = []
    for trkpt in root.findall(".//gpx:trkpt", _GPX_NS):
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        ele_el = trkpt.find("gpx:ele", _GPX_NS)
        time_el = trkpt.find("gpx:time", _GPX_NS)
        ele = float(ele_el.text) if ele_el is not None else 0.0
        ts = time_el.text if time_el is not None else ""
        points.append({"lat": lat, "lon": lon, "alt_m": ele, "time": ts})
    return points


def _parse_tcx(path: Path) -> list[dict]:
    tree = ET.parse(path)
    root = tree.getroot()
    points = []
    for tp in root.findall(".//tcx:Trackpoint", _TCX_NS):
        pos = tp.find("tcx:Position", _TCX_NS)
        alt_el = tp.find("tcx:AltitudeMeters", _TCX_NS)
        time_el = tp.find("tcx:Time", _TCX_NS)
        if pos is None:
            continue
        lat = float(pos.find("tcx:LatitudeDegrees", _TCX_NS).text)
        lon = float(pos.find("tcx:LongitudeDegrees", _TCX_NS).text)
        alt = float(alt_el.text) if alt_el is not None else 0.0
        ts = time_el.text if time_el is not None else ""
        points.append({"lat": lat, "lon": lon, "alt_m": alt, "time": ts})
    return points


def load_track(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".gpx":
        return _parse_gpx(path)
    elif suffix == ".tcx":
        return _parse_tcx(path)
    else:
        raise ValueError(f"Unsupported file format: {suffix}. Use .gpx or .tcx")


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000  # metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _parse_iso(ts: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S+00:00"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Thermal detection
# ---------------------------------------------------------------------------

def _detect_thermals(
    points: list[dict],
    segments: list[dict],
    min_vario_ms: float = 0.5,
    min_duration_s: int = 20,
) -> list[dict]:
    """
    Identify thermal climbs: consecutive segments with positive vario
    above threshold for at least min_duration_s.
    """
    thermals = []
    in_thermal = False
    thermal_start = None
    thermal_gain = 0.0

    for seg in segments:
        if seg["vario_ms"] >= min_vario_ms:
            if not in_thermal:
                in_thermal = True
                thermal_start = seg
                thermal_gain = 0.0
            thermal_gain += seg["alt_delta_m"]
        else:
            if in_thermal:
                duration = seg.get("dt_s", 0)
                if duration >= min_duration_s or thermal_gain > 50:
                    thermals.append(
                        {
                            "start_lat": thermal_start["lat1"],
                            "start_lon": thermal_start["lon1"],
                            "gain_m": round(thermal_gain, 1),
                        }
                    )
                in_thermal = False

    return thermals


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def analyze_track(path: Path) -> dict:
    """
    Parse a GPS track file and return performance metrics.

    Returns
    -------
    dict with: file, total_distance_km, max_speed_kmh, avg_ascent_rate_ms,
               avg_descent_rate_ms, total_ascent_m, total_descent_m,
               thermal_count, duration_s, points (raw), segments
    """
    points = load_track(path)
    if len(points) < 2:
        return {"error": "Not enough track points", "file": str(path)}

    segments = []
    total_dist_m = 0.0
    max_speed_ms = 0.0
    total_ascent = 0.0
    total_descent = 0.0
    ascent_rates = []
    descent_rates = []

    for i in range(1, len(points)):
        p0, p1 = points[i - 1], points[i]
        dist_m = _haversine_m(p0["lat"], p0["lon"], p1["lat"], p1["lon"])
        alt_delta = p1["alt_m"] - p0["alt_m"]

        t0 = _parse_iso(p0["time"])
        t1 = _parse_iso(p1["time"])
        dt_s = (t1 - t0).total_seconds() if t0 and t1 else 1.0
        dt_s = max(dt_s, 0.1)

        speed_ms = dist_m / dt_s
        vario_ms = alt_delta / dt_s

        if speed_ms > max_speed_ms:
            max_speed_ms = speed_ms

        if alt_delta > 0:
            total_ascent += alt_delta
            ascent_rates.append(vario_ms)
        elif alt_delta < 0:
            total_descent += abs(alt_delta)
            descent_rates.append(abs(vario_ms))

        total_dist_m += dist_m

        segments.append(
            {
                "lat1": p0["lat"],
                "lon1": p0["lon"],
                "lat2": p1["lat"],
                "lon2": p1["lon"],
                "dist_m": round(dist_m, 1),
                "alt_delta_m": round(alt_delta, 1),
                "speed_ms": round(speed_ms, 2),
                "vario_ms": round(vario_ms, 2),
                "dt_s": round(dt_s, 1),
            }
        )

    thermals = _detect_thermals(points, segments)

    t_start = _parse_iso(points[0]["time"])
    t_end = _parse_iso(points[-1]["time"])
    duration_s = (t_end - t_start).total_seconds() if t_start and t_end else 0

    return {
        "file": str(path),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "total_distance_km": round(total_dist_m / 1000, 3),
        "max_speed_kmh": round(max_speed_ms * 3.6, 2),
        "avg_ascent_rate_ms": round(sum(ascent_rates) / len(ascent_rates), 3) if ascent_rates else 0,
        "avg_descent_rate_ms": round(sum(descent_rates) / len(descent_rates), 3) if descent_rates else 0,
        "total_ascent_m": round(total_ascent, 1),
        "total_descent_m": round(total_descent, 1),
        "thermal_count": len(thermals),
        "thermals": thermals,
        "duration_s": round(duration_s, 1),
        "point_count": len(points),
    }


def export_csv(result: dict, csv_path: str):
    rows = [
        ["metric", "value"],
        ["total_distance_km", result["total_distance_km"]],
        ["max_speed_kmh", result["max_speed_kmh"]],
        ["avg_ascent_rate_ms", result["avg_ascent_rate_ms"]],
        ["avg_descent_rate_ms", result["avg_descent_rate_ms"]],
        ["total_ascent_m", result["total_ascent_m"]],
        ["total_descent_m", result["total_descent_m"]],
        ["thermal_count", result["thermal_count"]],
        ["duration_s", result["duration_s"]],
    ]
    with open(csv_path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"CSV exported to {csv_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="GPS track analysis for free-flight competitions.")
    parser.add_argument("--file", required=True, help="Path to .gpx or .tcx file")
    parser.add_argument("--csv", default="", help="Optional path to export CSV summary")
    args = parser.parse_args()

    result = analyze_track(Path(args.file))
    print(json.dumps(result, indent=2))

    if args.csv:
        export_csv(result, args.csv)


if __name__ == "__main__":
    main()
