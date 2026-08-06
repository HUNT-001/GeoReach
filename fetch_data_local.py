#!/usr/bin/env python3
"""
GeoReach — Local Data Fetch Script
===================================
Run this on YOUR machine (not sandbox) to download real data that
requires network access. It fetches:

1. Full OSM data via Overpass API (roads, settlements, hospitals,
   bridges, rivers) for Dhemaji + Lakhimpur + Majuli
2. SRTM DEM tiles (30m resolution) from OpenTopography
3. Saves everything to data/raw/ for the pipeline to auto-detect

Usage:
    python fetch_data_local.py            # fetch everything
    python fetch_data_local.py --osm      # only OSM data
    python fetch_data_local.py --dem      # only SRTM DEM
"""
import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FetchData")

PROJECT_ROOT = Path(__file__).parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"

# Focus area: Dhemaji + Lakhimpur + Majuli
BBOX = {"south": 26.65, "west": 93.6, "north": 27.85, "east": 95.3}

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
OPENTOPO_URL = "https://portal.opentopography.org/API/globaldem"


# ──────────────────────────────────────────────────────────────
# OSM via Overpass API
# ──────────────────────────────────────────────────────────────

def overpass_query(query_body: str, timeout: int = 300, max_attempts: int = 6) -> dict:
    """Send a query to the Overpass API and return JSON.

    Cycles through mirrors and retries on rate-limits / transient errors
    (504, IncompleteRead) with exponential backoff.
    """
    from urllib.parse import urlencode
    from http.client import IncompleteRead
    full_query = f"[out:json][timeout:{timeout}];{query_body}out body geom;"
    logger.info(f"  Sending Overpass query ({len(full_query)} chars)...")
    form_data = urlencode({"data": full_query}).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "GeoReach/1.0 (flood accessibility research)",
        "Accept": "application/json",
    }
    last_err = None
    for attempt in range(1, max_attempts + 1):
        # Round-robin the mirrors across attempts
        url = OVERPASS_MIRRORS[(attempt - 1) % len(OVERPASS_MIRRORS)]
        try:
            req = Request(url, data=form_data, method="POST", headers=headers)
            with urlopen(req, timeout=timeout + 30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            last_err = e
            if e.code in (429, 504, 503):
                wait = min(60, 10 * attempt)
                logger.warning(f"  {url} busy (HTTP {e.code}) — attempt {attempt}/{max_attempts}, waiting {wait}s...")
                time.sleep(wait)
                continue
            logger.warning(f"  {url} failed (HTTP {e.code}) — attempt {attempt}/{max_attempts}...")
            time.sleep(5)
            continue
        except (URLError, TimeoutError, IncompleteRead, ConnectionError) as e:
            last_err = e
            wait = min(60, 10 * attempt)
            logger.warning(f"  {url} error ({type(e).__name__}) — attempt {attempt}/{max_attempts}, waiting {wait}s...")
            time.sleep(wait)
            continue
    raise RuntimeError(f"All Overpass attempts failed. Last error: {last_err}")


def fetch_osm_roads():
    """Fetch road network."""
    logger.info("\n--- Fetching Roads ---")
    b = BBOX
    query = f"""
    (
      way["highway"~"primary|secondary|tertiary|trunk|residential|unclassified"]
        ({b['south']},{b['west']},{b['north']},{b['east']});
    );
    """
    result = overpass_query(query)
    elements = result.get("elements", [])
    logger.info(f"  Got {len(elements)} road elements")

    # Convert to GeoJSON
    features = []
    for el in elements:
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = [(n["lon"], n["lat"]) for n in el["geometry"]]
        if len(coords) < 2:
            continue
        tags = el.get("tags", {})
        features.append({
            "type": "Feature",
            "properties": {
                "osm_id": el["id"],
                "road_type": tags.get("highway", "unclassified"),
                "name": tags.get("name", ""),
                "surface": tags.get("surface", ""),
                "lanes": tags.get("lanes", ""),
            },
            "geometry": {"type": "LineString", "coordinates": coords}
        })

    geojson = {"type": "FeatureCollection", "features": features}
    out = RAW_DIR / "osm_roads.geojson"
    with open(out, "w") as f:
        json.dump(geojson, f)
    logger.info(f"  Saved {len(features)} roads → {out}")
    return len(features)


def fetch_osm_settlements():
    """Fetch settlement/place nodes."""
    logger.info("\n--- Fetching Settlements ---")
    b = BBOX
    query = f"""
    (
      node["place"~"city|town|village|hamlet"]
        ({b['south']},{b['west']},{b['north']},{b['east']});
    );
    """
    result = overpass_query(query)
    elements = result.get("elements", [])
    logger.info(f"  Got {len(elements)} settlement nodes")

    features = []
    for el in elements:
        if el.get("type") != "node":
            continue
        tags = el.get("tags", {})
        features.append({
            "type": "Feature",
            "properties": {
                "osm_id": el["id"],
                "name": tags.get("name", tags.get("name:en", "")),
                "place": tags.get("place", ""),
                "population": tags.get("population", ""),
            },
            "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        })

    geojson = {"type": "FeatureCollection", "features": features}
    out = RAW_DIR / "osm_settlements.geojson"
    with open(out, "w") as f:
        json.dump(geojson, f)
    logger.info(f"  Saved {len(features)} settlements → {out}")
    return len(features)


def fetch_osm_hospitals():
    """Fetch hospitals and health facilities."""
    logger.info("\n--- Fetching Hospitals ---")
    b = BBOX
    query = f"""
    (
      node["amenity"~"hospital|clinic|doctors"]
        ({b['south']},{b['west']},{b['north']},{b['east']});
      way["amenity"~"hospital|clinic|doctors"]
        ({b['south']},{b['west']},{b['north']},{b['east']});
    );
    """
    result = overpass_query(query)
    elements = result.get("elements", [])
    logger.info(f"  Got {len(elements)} health facility elements")

    features = []
    for el in elements:
        tags = el.get("tags", {})
        if el["type"] == "node":
            coords = [el["lon"], el["lat"]]
        elif "geometry" in el:
            lons = [n["lon"] for n in el["geometry"]]
            lats = [n["lat"] for n in el["geometry"]]
            coords = [sum(lons)/len(lons), sum(lats)/len(lats)]
        else:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "osm_id": el["id"],
                "name": tags.get("name", ""),
                "amenity": tags.get("amenity", "hospital"),
                "beds": tags.get("beds", ""),
            },
            "geometry": {"type": "Point", "coordinates": coords}
        })

    geojson = {"type": "FeatureCollection", "features": features}
    out = RAW_DIR / "osm_hospitals.geojson"
    with open(out, "w") as f:
        json.dump(geojson, f)
    logger.info(f"  Saved {len(features)} hospitals → {out}")
    return len(features)


def fetch_osm_rivers():
    """Fetch major waterways."""
    logger.info("\n--- Fetching Rivers ---")
    b = BBOX
    query = f"""
    (
      way["waterway"~"river|canal"]
        ({b['south']},{b['west']},{b['north']},{b['east']});
      relation["waterway"="river"]
        ({b['south']},{b['west']},{b['north']},{b['east']});
    );
    """
    result = overpass_query(query, timeout=600)
    elements = result.get("elements", [])
    logger.info(f"  Got {len(elements)} waterway elements")

    features = []
    for el in elements:
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = [(n["lon"], n["lat"]) for n in el["geometry"]]
        if len(coords) < 2:
            continue
        tags = el.get("tags", {})
        features.append({
            "type": "Feature",
            "properties": {
                "osm_id": el["id"],
                "name": tags.get("name", ""),
                "waterway": tags.get("waterway", "river"),
            },
            "geometry": {"type": "LineString", "coordinates": coords}
        })

    geojson = {"type": "FeatureCollection", "features": features}
    out = RAW_DIR / "osm_rivers.geojson"
    with open(out, "w") as f:
        json.dump(geojson, f)
    logger.info(f"  Saved {len(features)} waterways → {out}")
    return len(features)


def fetch_osm_bridges():
    """Fetch bridges."""
    logger.info("\n--- Fetching Bridges ---")
    b = BBOX
    query = f"""
    (
      way["bridge"="yes"]
        ({b['south']},{b['west']},{b['north']},{b['east']});
    );
    """
    result = overpass_query(query)
    elements = result.get("elements", [])
    logger.info(f"  Got {len(elements)} bridge elements")

    features = []
    for el in elements:
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = [(n["lon"], n["lat"]) for n in el["geometry"]]
        if len(coords) < 2:
            continue
        tags = el.get("tags", {})
        features.append({
            "type": "Feature",
            "properties": {
                "osm_id": el["id"],
                "name": tags.get("name", ""),
                "bridge": "yes",
                "highway": tags.get("highway", ""),
            },
            "geometry": {"type": "LineString", "coordinates": coords}
        })

    geojson = {"type": "FeatureCollection", "features": features}
    out = RAW_DIR / "osm_bridges.geojson"
    with open(out, "w") as f:
        json.dump(geojson, f)
    logger.info(f"  Saved {len(features)} bridges → {out}")
    return len(features)


def _already_have(filename):
    """Return True if an OSM layer was already downloaded (skip re-fetch)."""
    path = RAW_DIR / filename
    if path.exists() and path.stat().st_size > 100:
        logger.info(f"  [SKIP] {filename} already exists ({path.stat().st_size:,} bytes)")
        return True
    return False


def fetch_all_osm():
    """Fetch all OSM data layers (skips ones already downloaded)."""
    logger.info("=" * 60)
    logger.info("FETCHING OSM DATA — Dhemaji + Lakhimpur + Majuli")
    logger.info(f"Bbox: {BBOX}")
    logger.info("=" * 60)

    layers = [
        ("osm_roads.geojson", fetch_osm_roads, "roads"),
        ("osm_settlements.geojson", fetch_osm_settlements, "settlements"),
        ("osm_hospitals.geojson", fetch_osm_hospitals, "hospitals"),
        ("osm_rivers.geojson", fetch_osm_rivers, "rivers"),
        ("osm_bridges.geojson", fetch_osm_bridges, "bridges"),
    ]

    totals = {}
    for filename, fetch_fn, key in layers:
        logger.info(f"\n--- {key.title()} ---")
        if _already_have(filename):
            totals[key] = "cached"
            continue
        try:
            totals[key] = fetch_fn()
        except Exception as e:
            logger.error(f"  FAILED to fetch {key}: {e}")
            logger.error(f"  You can re-run the script to retry just {key}.")
            totals[key] = "FAILED"
        time.sleep(8)  # be nice to Overpass between layers

    logger.info("\n" + "=" * 60)
    logger.info("OSM FETCH COMPLETE")
    for k, v in totals.items():
        logger.info(f"  {k}: {v} features")
    logger.info("=" * 60)


# ──────────────────────────────────────────────────────────────
# District boundaries via Nominatim (returns polygons directly)
# ──────────────────────────────────────────────────────────────

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

def fetch_boundaries():
    """Download real Dhemaji / Lakhimpur / Majuli district polygons.

    Uses OSM Nominatim, which returns a GeoJSON polygon per district.
    Saves data/raw/admin_boundaries.geojson (used to CLIP all other data
    so neighbouring states no longer leak into the analysis).
    """
    from urllib.parse import urlencode
    logger.info("\n" + "=" * 60)
    logger.info("FETCHING DISTRICT BOUNDARIES (Dhemaji / Lakhimpur / Majuli)")
    logger.info("=" * 60)

    queries = [
        ("Dhemaji", "Dhemaji district, Assam, India"),
        ("Lakhimpur", "Lakhimpur district, Assam, India"),
        ("Majuli", "Majuli district, Assam, India"),
    ]

    features = []
    for district, q in queries:
        params = urlencode({
            "q": q, "format": "json", "polygon_geojson": 1,
            "limit": 1, "countrycodes": "in",
        })
        url = f"{NOMINATIM_URL}?{params}"
        logger.info(f"  Querying '{q}'...")
        try:
            req = Request(url, headers={"User-Agent": "GeoReach/1.0 (flood research)"})
            with urlopen(req, timeout=60) as resp:
                results = json.loads(resp.read().decode("utf-8"))
            if not results:
                logger.warning(f"    No boundary found for {district}")
                continue
            geom = results[0].get("geojson")
            if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
                logger.warning(f"    {district}: no polygon geometry returned "
                               f"(got {geom.get('type') if geom else None})")
                continue
            features.append({
                "type": "Feature",
                "properties": {"district": district,
                               "display_name": results[0].get("display_name", "")},
                "geometry": geom,
            })
            logger.info(f"    OK — {district} ({geom['type']})")
        except (HTTPError, URLError, TimeoutError) as e:
            logger.warning(f"    {district} failed: {e}")
        time.sleep(1.2)  # Nominatim rate policy: max 1 req/sec

    if not features:
        logger.error("  No boundaries fetched. District clipping unavailable.")
        return

    out = RAW_DIR / "admin_boundaries.geojson"
    with open(out, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)
    logger.info(f"  Saved {len(features)} district boundaries → {out}")


# ──────────────────────────────────────────────────────────────
# SRTM DEM via OpenTopography
# ──────────────────────────────────────────────────────────────

def fetch_srtm_dem():
    """Download SRTM 30m DEM from OpenTopography (free, no auth needed)."""
    logger.info("\n" + "=" * 60)
    logger.info("FETCHING SRTM DEM — 30m resolution")
    logger.info("=" * 60)

    b = BBOX
    # OpenTopography now requires a free personal API key (the old public demo
    # key is disabled). Get one in 1 min at:
    #   https://portal.opentopography.org/  ->  MyOpenTopo -> request API key
    # Then either set env var OPENTOPOGRAPHY_API_KEY, or paste it below.
    api_key = os.environ.get("OPENTOPOGRAPHY_API_KEY", "PASTE_YOUR_KEY_HERE")
    if api_key == "PASTE_YOUR_KEY_HERE":
        logger.warning("  No OpenTopography API key set.")
        logger.info("  EASIEST alternative: export the DEM from Google Earth Engine")
        logger.info("  using the script  dem_export_gee.js  (same workflow as the flood).")
        logger.info("  Or get a free key at https://portal.opentopography.org/ and set")
        logger.info("  OPENTOPOGRAPHY_API_KEY, then re-run: python fetch_data_local.py --dem")
        return

    url = (
        f"{OPENTOPO_URL}?demtype=SRTMGL1"
        f"&south={b['south']}&north={b['north']}"
        f"&west={b['west']}&east={b['east']}"
        f"&outputFormat=GTiff"
        f"&API_Key={api_key}"
    )

    out = RAW_DIR / "srtm_dem_dhemaji_lakhimpur_majuli.tif"
    logger.info(f"  Downloading from OpenTopography...")
    logger.info(f"  Area: {b['south']}N-{b['north']}N, {b['west']}E-{b['east']}E")

    try:
        req = Request(url, headers={"User-Agent": "GeoReach/1.0"})
        with urlopen(req, timeout=300) as resp:
            data = resp.read()
            with open(out, "wb") as f:
                f.write(data)
            size_mb = len(data) / (1024 * 1024)
            logger.info(f"  Saved DEM ({size_mb:.1f} MB) → {out}")
    except (HTTPError, URLError) as e:
        logger.warning(f"  OpenTopography failed: {e}")
        logger.info("  Trying NASA SRTM tiles directly...")
        fetch_srtm_tiles_nasa()


def fetch_srtm_tiles_nasa():
    """Fallback: fetch individual SRTM tiles from NASA/USGS (needs Earthdata login)."""
    logger.info("  NASA SRTM requires an Earthdata account.")
    logger.info("  Register at: https://urs.earthdata.nasa.gov/users/new")
    logger.info("  Then download these tiles manually:")
    # Tiles needed for our bbox
    tiles = ["N26E093", "N26E094", "N26E095", "N27E093", "N27E094", "N27E095"]
    for t in tiles:
        logger.info(f"    https://e4ftl01.cr.usgs.gov/MEASURES/SRTMGL1.003/2000.02.11/{t}.SRTMGL1.hgt.zip")
    logger.info(f"  Save .hgt files to: {RAW_DIR}")


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GeoReach — Download Real Geospatial Data")
    parser.add_argument("--osm", action="store_true", help="Fetch only OSM data")
    parser.add_argument("--dem", action="store_true", help="Fetch only SRTM DEM")
    parser.add_argument("--boundaries", action="store_true", help="Fetch only district boundaries")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    fetch_all = not args.osm and not args.dem and not args.boundaries

    if args.boundaries or fetch_all:
        fetch_boundaries()

    if args.osm or fetch_all:
        fetch_all_osm()

    if args.dem or fetch_all:
        fetch_srtm_dem()

    logger.info("\n" + "=" * 60)
    logger.info("ALL DOWNLOADS COMPLETE")
    logger.info(f"Data saved to: {RAW_DIR}")
    logger.info("")
    logger.info("Next: run the pipeline with --no-fetch:")
    logger.info("  cd src && python main.py --scenario high --no-fetch")
    logger.info("")
    logger.info("OPTIONAL — exact village populations (Census 2011):")
    logger.info("  Download Assam village PCA from censusindia.gov.in,")
    logger.info("  save as data/raw/census_pca_assam.csv, then re-run the pipeline.")
    logger.info("  Columns needed: Name, District, TRU, TOT_P")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
