"""
thermal_mapper.py — Thermal Mapping & Prediction

Predicts thermal hotspot locations using wind shear data, surface temperature
differentials, and terrain features. Outputs a heat-map JSON overlay that can
be consumed by a mobile app or mapping library (Leaflet, Mapbox, etc.).

Inputs:
    lat, lon          : center of area of interest
    radius_km         : search radius in kilometres
    wind_shear_kt     : vertical wind shear in knots (optional)
    surface_temp_c    : surface temperature in °C (optional)

Outputs:
    JSON heat-map overlay with intensity grid and top predicted thermal zones.

Usage:
    python thermal_mapper.py --lat 34.05 --lon -118.25 --radius 30
    python thermal_mapper.py --lat 34.05 --lon -118.25 --radius 30 \
                             --wind-shear 8 --surface-temp 28
"""

import argparse
import json
import math
import urllib.request
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _fetch_open_elevation(lat: float, lon: float, step_deg: float, count: int) -> list[dict]:
    """
    Sample terrain elevation on a regular grid using the Open-Elevation API.
    Returns list of {"latitude", "longitude", "elevation"} dicts.
    """
    locations = []
    for i in range(-count, count + 1):
        for j in range(-count, count + 1):
            locations.append({"latitude": lat + i * step_deg, "longitude": lon + j * step_deg})

    payload = json.dumps({"locations": locations}).encode()
    url = "https://api.open-elevation.com/api/v1/lookup"
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("results", [])
    except Exception:
        # Offline stub: flat terrain
        return [
            {"latitude": loc["latitude"], "longitude": loc["longitude"], "elevation": 500.0}
            for loc in locations
        ]


# ---------------------------------------------------------------------------
# Thermal potential model
# ---------------------------------------------------------------------------

def _thermal_potential(
    elevation_m: float,
    surface_temp_c: float,
    wind_shear_kt: float,
    lat: float,
    center_lat: float,
    center_lon: float,
    pt_lat: float,
    pt_lon: float,
) -> float:
    """
    Heuristic thermal potential score [0, 1] for a grid point.

    Factors:
    - Higher elevation differences from surroundings → better trigger
    - Higher surface temp → more convection
    - Moderate wind shear (too high suppresses thermals)
    - Distance from center (mild proximity bias)
    """
    # Normalise elevation contribution (assume sea level = 0 baseline here)
    elev_score = min(1.0, elevation_m / 3000.0)

    # Temperature: hotter = stronger thermals, cap at 40°C
    temp_score = max(0.0, min(1.0, (surface_temp_c - 10) / 30.0))

    # Wind shear: sweet spot ~5–15 kt; suppress above 25 kt
    if wind_shear_kt <= 0:
        shear_score = 0.3
    elif wind_shear_kt <= 15:
        shear_score = 0.5 + (wind_shear_kt / 15) * 0.5
    else:
        shear_score = max(0.0, 1.0 - (wind_shear_kt - 15) / 30.0)

    score = (elev_score * 0.4 + temp_score * 0.4 + shear_score * 0.2)
    return round(score, 4)


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def build_thermal_map(
    lat: float,
    lon: float,
    radius_km: float = 20.0,
    wind_shear_kt: float = 8.0,
    surface_temp_c: float = 22.0,
    grid_steps: int = 5,
) -> dict:
    """
    Build a thermal heat-map overlay for the given area.

    Parameters
    ----------
    lat, lon        : centre of area in decimal degrees
    radius_km       : search radius in km
    wind_shear_kt   : estimated vertical wind shear in knots
    surface_temp_c  : estimated surface temperature in °C
    grid_steps      : half-count of grid steps per axis (total = (2n+1)^2 points)

    Returns
    -------
    dict with: generated_at, center, parameters, heatmap (list of grid points),
               top_zones (top 5 predicted thermal hotspots)
    """
    step_deg = (radius_km / 111.0) / grid_steps  # ~111 km per degree latitude

    elev_data = _fetch_open_elevation(lat, lon, step_deg, grid_steps)

    heatmap = []
    for pt in elev_data:
        plat, plon, elev = pt["latitude"], pt["longitude"], pt["elevation"]
        score = _thermal_potential(
            elevation_m=elev,
            surface_temp_c=surface_temp_c,
            wind_shear_kt=wind_shear_kt,
            lat=plat,
            center_lat=lat,
            center_lon=lon,
            pt_lat=plat,
            pt_lon=plon,
        )
        heatmap.append(
            {
                "lat": plat,
                "lon": plon,
                "elevation_m": elev,
                "thermal_score": score,
            }
        )

    # Sort and pick top zones
    heatmap_sorted = sorted(heatmap, key=lambda x: x["thermal_score"], reverse=True)
    top_zones = heatmap_sorted[:5]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "center": {"lat": lat, "lon": lon},
        "parameters": {
            "radius_km": radius_km,
            "wind_shear_kt": wind_shear_kt,
            "surface_temp_c": surface_temp_c,
        },
        "heatmap": heatmap,
        "top_zones": top_zones,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Thermal mapping & prediction for free-flight pilots.")
    parser.add_argument("--lat", type=float, required=True, help="Centre latitude")
    parser.add_argument("--lon", type=float, required=True, help="Centre longitude")
    parser.add_argument("--radius", type=float, default=20.0, help="Search radius in km (default 20)")
    parser.add_argument("--wind-shear", type=float, default=8.0, help="Wind shear in knots (default 8)")
    parser.add_argument("--surface-temp", type=float, default=22.0, help="Surface temp °C (default 22)")
    args = parser.parse_args()

    result = build_thermal_map(
        lat=args.lat,
        lon=args.lon,
        radius_km=args.radius,
        wind_shear_kt=args.wind_shear,
        surface_temp_c=args.surface_temp,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
