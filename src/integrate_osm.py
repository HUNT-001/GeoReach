"""
Integrate Downloaded OSM Data into GeoReach Pipeline
====================================================
Loads the real OSM data downloaded by fetch_data_local.py
(osm_roads.geojson, osm_settlements.geojson, etc.) and enriches it:

- Roads: assigns speed limits + travel times by highway type
- Settlements: fills population from census where known, else estimates
  by place type; tags each with its district
- Hospitals: keeps all real facilities
- Rivers/Bridges: loaded as-is

Falls back to build_real_data.py's hand-built data if OSM files absent.
"""
import os
import logging
import geopandas as gpd
import numpy as np
from shapely.geometry import Point

from config_loader import get_config
from build_real_data import (
    SETTLEMENTS, HOSPITALS, DISTRICTS, STUDY_BBOX,
    build_water_level_data, build_asdma_data, save_gdf_geojson,
)

logger = logging.getLogger("OSMIntegrator")

# Population estimates by OSM place type (when no census/population tag)
PLACE_POP_DEFAULT = {
    "city": 100000,
    "town": 15000,
    "village": 2000,
    "hamlet": 400,
    "suburb": 5000,
    "neighbourhood": 1500,
    "isolated_dwelling": 50,
    "locality": 200,
}

# Known populations from census research (name -> population)
KNOWN_POP = {s["name"].lower(): s["pop"] for s in SETTLEMENTS}


def osm_files_present(raw_dir):
    """Check if the core downloaded OSM files exist."""
    required = ["osm_roads.geojson", "osm_settlements.geojson", "osm_hospitals.geojson"]
    return all(os.path.exists(os.path.join(raw_dir, f)) for f in required)


def load_boundaries(raw_dir):
    """Load real district boundary polygons if downloaded, else None."""
    path = os.path.join(raw_dir, "admin_boundaries.geojson")
    if not os.path.exists(path):
        return None
    try:
        gdf = gpd.read_file(path)
        if gdf.empty:
            return None
        logger.info(f"  District boundaries found: {list(gdf['district'])}")
        return gdf.to_crs("EPSG:4326")
    except Exception as e:
        logger.warning(f"  Could not read boundaries: {e}")
        return None


def clip_to_boundaries(gdf, boundaries, layer_name="layer"):
    """Clip a GeoDataFrame to the union of district boundaries and tag each
    feature with the district polygon it falls in (for points/lines)."""
    if boundaries is None or gdf is None or gdf.empty:
        return gdf
    union = boundaries.geometry.unary_union
    before = len(gdf)
    # Keep features that intersect the district area
    gdf = gdf[gdf.geometry.intersects(union)].copy()
    logger.info(f"  Clipped {layer_name}: {before} -> {len(gdf)} within districts")
    return gdf.reset_index(drop=True)


def assign_district_by_polygon(gdf, boundaries):
    """Assign 'district' by actual polygon containment (points/representative pts)."""
    if boundaries is None or gdf is None or gdf.empty:
        return gdf
    reps = gdf.copy()
    reps["_rep"] = reps.geometry.representative_point()
    reps = reps.set_geometry("_rep")
    joined = gpd.sjoin(
        reps[["_rep"]], boundaries[["geometry", "district"]],
        how="left", predicate="within"
    )
    dist = joined.groupby(level=0)["district"].first()
    gdf = gdf.copy()
    gdf["district"] = dist.reindex(gdf.index).fillna(
        gdf.geometry.apply(lambda g: assign_district(g.centroid.x, g.centroid.y))
    ).values
    return gdf


def assign_district(lon, lat):
    """Assign a settlement to the nearest district by center distance."""
    best, best_d = None, 1e9
    for name, info in DISTRICTS.items():
        cx, cy = info["center"]
        d = (lon - cx) ** 2 + (lat - cy) ** 2
        if d < best_d:
            best_d, best = d, name
    return best


def load_osm_roads(raw_dir):
    """Load and enrich OSM roads with speed + travel time."""
    cfg = get_config()
    speed_limits = cfg["network"]["speed_limits"]
    path = os.path.join(raw_dir, "osm_roads.geojson")
    roads = gpd.read_file(path)
    logger.info(f"  Loaded {len(roads)} OSM road segments")

    # Normalize road_type column (OSM 'highway' values, keep base type)
    def base_type(rt):
        if not isinstance(rt, str):
            return "unclassified"
        rt = rt.split("_")[0]  # primary_link -> primary
        return rt

    roads["road_type"] = roads["road_type"].apply(base_type)
    roads["speed_kmh"] = roads["road_type"].map(
        lambda t: speed_limits.get(t, 20)
    )
    # Length in meters (project to UTM 46N for accuracy)
    roads_utm = roads.to_crs("EPSG:32646")
    roads["length_m"] = roads_utm.geometry.length.round(1)
    roads["travel_time_min"] = (
        (roads["length_m"] / 1000) / roads["speed_kmh"] * 60
    ).round(2)
    return roads


def load_osm_settlements(raw_dir):
    """Load and enrich OSM settlements with population + district."""
    path = os.path.join(raw_dir, "osm_settlements.geojson")
    sett = gpd.read_file(path)
    logger.info(f"  Loaded {len(sett)} OSM settlements")

    def osm_pop_tag(row):
        pop_tag = row.get("population", "")
        try:
            if pop_tag and str(pop_tag).strip():
                return int(float(str(pop_tag).replace(",", "")))
        except (ValueError, TypeError):
            pass
        return None

    # Provisional population; finalised (district-aware + census) later.
    def estimate_pop(row):
        name = str(row.get("name", "")).lower()
        if name in KNOWN_POP:
            return KNOWN_POP[name]
        tag = osm_pop_tag(row)
        if tag is not None:
            return tag
        return PLACE_POP_DEFAULT.get(row.get("place", "village"), 1000)

    sett["osm_pop_tag"] = sett.apply(lambda r: osm_pop_tag(r) or 0, axis=1)
    sett["est_population"] = sett.apply(estimate_pop, axis=1)
    sett["settlement_type"] = sett["place"]
    sett["district"] = sett.geometry.apply(
        lambda p: assign_district(p.x, p.y)
    )
    # Drop unnamed places to reduce noise (keep only named settlements)
    named = sett[sett["name"].astype(str).str.strip() != ""].copy()
    logger.info(f"  {len(named)} named settlements retained")
    return named.reset_index(drop=True)


def finalize_populations(settlements_gdf, raw_dir):
    """Finalise settlement populations after district assignment.

    Priority: census real match > OSM population tag > known census town >
    district-aware place-type default (realistic rural averages).
    """
    from census_join import join_population, district_default_pops
    gdf = settlements_gdf.copy()

    # 1) District-aware defaults for places that only had a flat guess
    dd = district_default_pops()
    def better_default(row):
        name = str(row.get("name", "")).lower()
        if name in KNOWN_POP:
            return KNOWN_POP[name]
        if row.get("osm_pop_tag", 0):
            return int(row["osm_pop_tag"])
        place = row.get("settlement_type", "village")
        dist = row.get("district", "")
        if place in ("city", "town", "suburb"):
            return PLACE_POP_DEFAULT.get(place, 8000)
        d = dd.get(dist, {})
        return d.get(place, d.get("village", 900))
    gdf["est_population"] = gdf.apply(better_default, axis=1).astype(int)

    # 2) Overwrite with REAL Census figures where available
    gdf, matched = join_population(gdf, raw_dir)
    if matched == 0:
        logger.info("  (No census_pca_assam.csv — using district-aware estimates. "
                    "Add the file to data/raw/ for exact village populations.)")
    if "osm_pop_tag" in gdf.columns:
        gdf = gdf.drop(columns=["osm_pop_tag"])
    return gdf


def load_osm_hospitals(raw_dir):
    """Load OSM hospitals/health facilities."""
    path = os.path.join(raw_dir, "osm_hospitals.geojson")
    hosp = gpd.read_file(path)
    logger.info(f"  Loaded {len(hosp)} OSM health facilities")

    hosp["facility_type"] = hosp["amenity"]
    hosp["district"] = hosp.geometry.apply(
        lambda p: assign_district(p.x, p.y)
    )
    # Beds: use OSM tag if present else default by type
    def beds(row):
        b = row.get("beds", "")
        try:
            if b and str(b).strip():
                return int(float(b))
        except (ValueError, TypeError):
            pass
        return {"hospital": 50, "clinic": 15, "doctors": 5}.get(
            row.get("amenity", "clinic"), 10
        )
    hosp["beds"] = hosp.apply(beds, axis=1)
    return hosp


def load_osm_rivers(raw_dir):
    """Load OSM rivers, fall back to hand-built if absent."""
    path = os.path.join(raw_dir, "osm_rivers.geojson")
    if os.path.exists(path):
        rivers = gpd.read_file(path)
        logger.info(f"  Loaded {len(rivers)} OSM waterway segments")
        return rivers
    from build_real_data import build_rivers
    return build_rivers()


def load_osm_bridges(raw_dir):
    """Load OSM bridges, fall back to hand-built if absent."""
    path = os.path.join(raw_dir, "osm_bridges.geojson")
    if os.path.exists(path):
        bridges = gpd.read_file(path)
        logger.info(f"  Loaded {len(bridges)} OSM bridges")
        return bridges
    from build_real_data import build_bridges
    return build_bridges()


def load_real_osm_datasets():
    """Load all downloaded OSM data, enriched and ready for the pipeline."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw_dir = os.path.join(project_root, "data", "raw")

    logger.info("=" * 60)
    logger.info("LOADING REAL OSM DATA (downloaded via fetch_data_local.py)")
    logger.info("=" * 60)

    # Load real district boundaries if available (enables clipping)
    boundaries = load_boundaries(raw_dir)

    logger.info("\n--- Roads ---")
    roads = load_osm_roads(raw_dir)
    logger.info("\n--- Settlements ---")
    settlements = load_osm_settlements(raw_dir)
    logger.info("\n--- Hospitals ---")
    hospitals = load_osm_hospitals(raw_dir)
    logger.info("\n--- Rivers ---")
    rivers = load_osm_rivers(raw_dir)
    logger.info("\n--- Bridges ---")
    bridges = load_osm_bridges(raw_dir)

    # ── Clip everything to the 3 real districts (removes neighbouring states) ──
    if boundaries is not None:
        logger.info("\n--- Clipping to real district boundaries ---")
        roads = clip_to_boundaries(roads, boundaries, "roads")
        settlements = clip_to_boundaries(settlements, boundaries, "settlements")
        settlements = assign_district_by_polygon(settlements, boundaries)
        hospitals = clip_to_boundaries(hospitals, boundaries, "hospitals")
        hospitals = assign_district_by_polygon(hospitals, boundaries)
        rivers = clip_to_boundaries(rivers, boundaries, "rivers")
        bridges = clip_to_boundaries(bridges, boundaries, "bridges")
    else:
        logger.info("\n  [No admin_boundaries.geojson] Districts assigned by nearest "
                    "center — run 'python fetch_data_local.py --boundaries' to clip "
                    "precisely to Dhemaji/Lakhimpur/Majuli.")

    # Finalise populations (district-aware defaults + real Census join)
    logger.info("\n--- Finalising populations ---")
    settlements = finalize_populations(settlements, raw_dir)

    # Water level + ASDMA (from research, no OSM equivalent)
    water_levels = build_water_level_data()
    asdma = build_asdma_data()

    # Report district breakdown
    logger.info("\nDistrict breakdown (settlements):")
    for d in DISTRICTS:
        sub = settlements[settlements["district"] == d]
        logger.info(f"  {d}: {len(sub)} settlements, pop ~{sub['est_population'].sum():,}")

    logger.info("\n" + "=" * 60)
    logger.info("REAL OSM DATA LOADED")
    logger.info(f"  Roads: {len(roads)}  Settlements: {len(settlements)}  "
                f"Hospitals: {len(hospitals)}")
    logger.info("=" * 60)

    return {
        "roads": roads,
        "settlements": settlements,
        "hospitals": hospitals,
        "rivers": rivers,
        "bridges": bridges,
        "water_levels": water_levels,
        "asdma": asdma,
        "bbox": STUDY_BBOX,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    data = load_real_osm_datasets()
