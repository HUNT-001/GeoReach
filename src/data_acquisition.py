"""
Data Acquisition Module for GeoReach
Fetches road networks, settlements, hospitals, bridges, and other infrastructure
from OpenStreetMap for the Assam study area.
"""
import os
import json
import logging
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import box, Point, LineString, Polygon, MultiPolygon
from shapely.ops import unary_union
import requests

from config_loader import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def overpass_query(query, timeout=180):
    """Execute an Overpass API query and return raw JSON."""
    full_query = f"[out:json][timeout:{timeout}];{query}out body geom;"
    resp = requests.get(OVERPASS_URL, params={"data": full_query}, timeout=timeout + 30)
    resp.raise_for_status()
    return resp.json()


def _elements_to_gdf(elements, geom_type="point"):
    """Convert Overpass elements to a GeoDataFrame."""
    features = []
    for el in elements:
        tags = el.get("tags", {})
        if geom_type == "point":
            if "lat" in el and "lon" in el:
                geom = Point(el["lon"], el["lat"])
            elif "center" in el:
                geom = Point(el["center"]["lon"], el["center"]["lat"])
            else:
                continue
        elif geom_type == "line":
            if "geometry" in el:
                coords = [(n["lon"], n["lat"]) for n in el["geometry"]]
                if len(coords) >= 2:
                    geom = LineString(coords)
                else:
                    continue
            else:
                continue
        elif geom_type == "polygon":
            if "geometry" in el:
                coords = [(n["lon"], n["lat"]) for n in el["geometry"]]
                if len(coords) >= 4:
                    geom = Polygon(coords)
                else:
                    continue
            else:
                continue
        else:
            continue
        props = {"osm_id": el.get("id"), "type": el.get("type")}
        props.update(tags)
        features.append({"geometry": geom, **props})

    if not features:
        return gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
    return gpd.GeoDataFrame(features, crs="EPSG:4326")


def fetch_road_network(bbox, road_types=None):
    """Fetch road network from OSM for the given bounding box."""
    cfg = get_config()
    if road_types is None:
        road_types = cfg["data_sources"]["osm"]["road_types"]

    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]
    highway_filter = "|".join(road_types)

    query = f"""
    (
      way["highway"~"^({highway_filter})$"]({s},{w},{n},{e});
    );
    """
    logger.info("Fetching road network from OSM...")
    data = overpass_query(query)
    elements = data.get("elements", [])
    logger.info(f"  Retrieved {len(elements)} road segments")

    gdf = _elements_to_gdf(elements, geom_type="line")
    if not gdf.empty:
        gdf = gdf.rename(columns={"highway": "road_type"})
        # Assign speed limits
        speed_limits = cfg["network"]["speed_limits"]
        gdf["speed_kmh"] = gdf["road_type"].map(speed_limits).fillna(20)
        gdf["length_m"] = gdf.to_crs(cfg["study_area"]["crs"]).geometry.length
        gdf["travel_time_min"] = (gdf["length_m"] / 1000) / gdf["speed_kmh"] * 60

    return gdf


def fetch_settlements(bbox):
    """Fetch settlement/village boundaries and points from OSM."""
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]

    query = f"""
    (
      node["place"~"^(city|town|village|hamlet)$"]({s},{w},{n},{e});
      way["place"~"^(city|town|village|hamlet)$"]({s},{w},{n},{e});
      relation["place"~"^(city|town|village|hamlet)$"]({s},{w},{n},{e});
    );
    """
    logger.info("Fetching settlements from OSM...")
    data = overpass_query(query)
    elements = data.get("elements", [])
    logger.info(f"  Retrieved {len(elements)} settlements")

    gdf = _elements_to_gdf(elements, geom_type="point")
    if not gdf.empty and "place" in gdf.columns:
        gdf = gdf.rename(columns={"place": "settlement_type"})
        # Estimate population based on settlement type
        pop_estimates = {"city": 100000, "town": 20000, "village": 2000, "hamlet": 500}
        gdf["est_population"] = gdf["settlement_type"].map(pop_estimates).fillna(1000)
        # Add some randomness for realism
        rng = np.random.default_rng(42)
        gdf["est_population"] = (gdf["est_population"] * rng.uniform(0.5, 1.5, len(gdf))).astype(int)

    return gdf


def fetch_hospitals(bbox):
    """Fetch hospitals and health facilities from OSM."""
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]

    query = f"""
    (
      node["amenity"="hospital"]({s},{w},{n},{e});
      way["amenity"="hospital"]({s},{w},{n},{e});
      node["amenity"="clinic"]({s},{w},{n},{e});
      way["amenity"="clinic"]({s},{w},{n},{e});
      node["amenity"="doctors"]({s},{w},{n},{e});
    );
    """
    logger.info("Fetching hospitals from OSM...")
    data = overpass_query(query)
    elements = data.get("elements", [])
    logger.info(f"  Retrieved {len(elements)} health facilities")
    return _elements_to_gdf(elements, geom_type="point")


def fetch_bridges(bbox):
    """Fetch bridges from OSM."""
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]

    query = f"""
    (
      way["bridge"="yes"]({s},{w},{n},{e});
      way["man_made"="bridge"]({s},{w},{n},{e});
    );
    """
    logger.info("Fetching bridges from OSM...")
    data = overpass_query(query)
    elements = data.get("elements", [])
    logger.info(f"  Retrieved {len(elements)} bridges")
    return _elements_to_gdf(elements, geom_type="line")


def fetch_rivers(bbox):
    """Fetch major rivers and waterways from OSM."""
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]

    query = f"""
    (
      way["waterway"~"^(river|canal)$"]({s},{w},{n},{e});
      relation["waterway"="river"]({s},{w},{n},{e});
    );
    """
    logger.info("Fetching rivers from OSM...")
    data = overpass_query(query)
    elements = data.get("elements", [])
    logger.info(f"  Retrieved {len(elements)} waterways")
    return _elements_to_gdf(elements, geom_type="line")


def fetch_admin_boundaries(bbox):
    """Fetch district-level administrative boundaries for Assam."""
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]

    query = f"""
    (
      relation["admin_level"="5"]["boundary"="administrative"]({s},{w},{n},{e});
    );
    """
    logger.info("Fetching administrative boundaries from OSM...")
    data = overpass_query(query)
    elements = data.get("elements", [])
    logger.info(f"  Retrieved {len(elements)} admin boundaries")

    # For relations, extract outer ways as polygon
    features = []
    for el in elements:
        if el.get("type") == "relation" and "members" in el:
            tags = el.get("tags", {})
            coords = []
            for member in el["members"]:
                if member.get("role") == "outer" and "geometry" in member:
                    coords.extend([(n["lon"], n["lat"]) for n in member["geometry"]])
            if len(coords) >= 4:
                try:
                    geom = Polygon(coords)
                    if geom.is_valid:
                        props = {"osm_id": el["id"], "name": tags.get("name", "Unknown")}
                        props.update(tags)
                        features.append({"geometry": geom, **props})
                except Exception:
                    pass

    if not features:
        return gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
    return gpd.GeoDataFrame(features, crs="EPSG:4326")


def generate_synthetic_population(settlements_gdf, bbox):
    """Generate synthetic population density grid."""
    cfg = get_config()
    if settlements_gdf.empty:
        return gpd.GeoDataFrame(columns=["geometry", "population_density"], crs="EPSG:4326")

    # Create a grid of points
    rng = np.random.default_rng(42)
    grid_size = 0.1  # ~11km grid cells
    x_range = np.arange(bbox[0], bbox[2], grid_size)
    y_range = np.arange(bbox[1], bbox[3], grid_size)

    cells = []
    for x in x_range:
        for y in y_range:
            cell = box(x, y, x + grid_size, y + grid_size)
            # Calculate population density based on proximity to settlements
            centroid = cell.centroid
            min_dist = settlements_gdf.geometry.distance(centroid).min()
            # Inverse distance weighting for density
            base_density = max(10, 500 * np.exp(-min_dist * 10))
            density = base_density * rng.uniform(0.7, 1.3)
            cells.append({"geometry": cell, "population_density": round(density, 1)})

    return gpd.GeoDataFrame(cells, crs="EPSG:4326")


def save_dataset(gdf, name, output_dir):
    """Save a GeoDataFrame to GeoJSON."""
    if gdf.empty:
        logger.warning(f"  {name} is empty, skipping save")
        return
    path = os.path.join(output_dir, f"{name}.geojson")
    with open(path, "w") as f:
        f.write(gdf.to_json())
    logger.info(f"  Saved {name}: {len(gdf)} features -> {path}")


def run_acquisition(use_subset=True):
    """Run the full data acquisition pipeline.

    Args:
        use_subset: If True, use a smaller bbox for faster testing.
    """
    cfg = get_config()
    bbox = cfg["study_area"]["bbox"]

    if use_subset:
        # Use a smaller area around Guwahati/Kamrup for faster testing
        # Approximately 1 degree x 1 degree area
        bbox = [91.5, 26.0, 92.5, 26.8]
        logger.info(f"Using subset bbox: {bbox}")
    else:
        logger.info(f"Using full Assam bbox: {bbox}")

    raw_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           cfg["paths"]["raw_data"])
    os.makedirs(raw_dir, exist_ok=True)

    # Fetch all datasets
    roads = fetch_road_network(bbox)
    settlements = fetch_settlements(bbox)
    hospitals = fetch_hospitals(bbox)
    bridges = fetch_bridges(bbox)
    rivers = fetch_rivers(bbox)
    admin = fetch_admin_boundaries(bbox)

    # Save raw data
    save_dataset(roads, "roads", raw_dir)
    save_dataset(settlements, "settlements", raw_dir)
    save_dataset(hospitals, "hospitals", raw_dir)
    save_dataset(bridges, "bridges", raw_dir)
    save_dataset(rivers, "rivers", raw_dir)
    save_dataset(admin, "admin_boundaries", raw_dir)

    # Generate synthetic population grid
    pop_grid = generate_synthetic_population(settlements, bbox)
    save_dataset(pop_grid, "population_grid", raw_dir)

    logger.info("Data acquisition complete!")
    return {
        "roads": roads,
        "settlements": settlements,
        "hospitals": hospitals,
        "bridges": bridges,
        "rivers": rivers,
        "admin_boundaries": admin,
        "population_grid": pop_grid,
        "bbox": bbox
    }


if __name__ == "__main__":
    run_acquisition(use_subset=True)
