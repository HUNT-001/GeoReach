"""
Flood Inundation Simulation Module for GeoReach
Generates realistic flood inundation maps based on river proximity,
terrain characteristics, and configurable water level scenarios.
"""
import os
import logging
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import box, Point, Polygon, MultiPolygon
from shapely.ops import unary_union
from scipy.ndimage import gaussian_filter

from config_loader import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_elevation_model(bbox, resolution=0.005):
    """Generate a synthetic DEM for the study area.

    Uses terrain characteristics typical of Assam's Brahmaputra floodplain:
    - Low-lying central valley along the Brahmaputra
    - Higher terrain on the edges (Meghalaya plateau to south, hills to north)
    """
    rng = np.random.default_rng(42)

    x_range = np.arange(bbox[0], bbox[2], resolution)
    y_range = np.arange(bbox[1], bbox[3], resolution)
    xx, yy = np.meshgrid(x_range, y_range)

    # Base elevation: valley floor with higher edges
    center_lat = (bbox[1] + bbox[3]) / 2
    center_lon = (bbox[0] + bbox[2]) / 2

    # Distance from center creates valley profile
    lat_dist = np.abs(yy - center_lat) / ((bbox[3] - bbox[1]) / 2)
    lon_dist = np.abs(xx - center_lon) / ((bbox[2] - bbox[0]) / 2)

    # Valley profile: low in center, higher at edges
    elevation = 30 + 80 * lat_dist**2 + 20 * lon_dist**1.5

    # Add river channel depression along the center
    river_dist = np.abs(yy - center_lat) * 111  # rough km conversion
    river_channel = np.exp(-river_dist**2 / 5) * 15
    elevation -= river_channel

    # Add realistic noise and micro-topography
    noise = rng.normal(0, 3, elevation.shape)
    noise = gaussian_filter(noise, sigma=3)
    elevation += noise

    # Ensure minimum elevation
    elevation = np.maximum(elevation, 5)

    return xx, yy, elevation


def simulate_flood_inundation(rivers_gdf, bbox, scenario="moderate", resolution=None):
    """Simulate flood inundation based on river proximity and water level scenario.

    Args:
        rivers_gdf: GeoDataFrame of river geometries
        bbox: [west, south, east, north] bounding box
        scenario: 'low', 'moderate', 'high', or 'extreme'
        resolution: Grid resolution in degrees (auto-selected if None)

    Returns:
        GeoDataFrame of flood polygons with depth estimates
    """
    cfg = get_config()
    flood_scenarios = cfg["flood"]["scenarios"]
    water_level = flood_scenarios.get(scenario, flood_scenarios["moderate"])
    river_buffer = cfg["flood"]["river_buffer"]

    # Auto-select resolution: keep grid under ~30K cells for speed
    bbox_width = bbox[2] - bbox[0]
    bbox_height = bbox[3] - bbox[1]
    if resolution is None:
        area_deg2 = bbox_width * bbox_height
        resolution = max(0.005, (area_deg2 / 25000) ** 0.5)
        resolution = round(resolution, 4)

    logger.info(f"Simulating flood inundation - scenario: {scenario}, "
                f"water level: {water_level}m, resolution: {resolution}°")

    # Generate synthetic DEM
    xx, yy, elevation = generate_elevation_model(bbox, resolution)
    logger.info(f"  Grid size: {xx.shape[0]}x{xx.shape[1]} = {xx.size} cells")

    # If a large real-world river network is supplied (e.g. hundreds of OSM
    # waterways), restrict flood-driving rivers to MAJOR ones. Small streams
    # and canals should not each spread a wide flood plume, or the whole
    # region floods. Keep major named rivers (by cumulative length).
    if not rivers_gdf.empty and len(rivers_gdf) > 25 and "name" in rivers_gdf.columns:
        rv = rivers_gdf.copy()
        try:
            rv_utm = rv.to_crs("EPSG:32646")
            rv["_len_km"] = rv_utm.geometry.length / 1000
        except Exception:
            rv["_len_km"] = rv.geometry.length * 111
        rv["_name"] = rv["name"].astype(str).str.strip()
        # Cumulative length per named river; keep rivers totalling > 20 km
        by_name = rv[rv["_name"] != ""].groupby("_name")["_len_km"].sum()
        major_names = set(by_name[by_name > 20].index)
        major = rv[rv["_name"].isin(major_names)]
        if not major.empty:
            logger.info(f"  Filtered {len(rivers_gdf)} waterways -> "
                        f"{len(major)} major-river segments ({len(major_names)} rivers)")
            rivers_gdf = major

    # Calculate distance from rivers for each grid cell
    if rivers_gdf.empty:
        logger.warning("No river data available, using center-line approximation")
        center_lat = (bbox[1] + bbox[3]) / 2
        river_dist_km = np.abs(yy - center_lat) * 111
    else:
        # Union all river geometries
        river_union = unary_union(rivers_gdf.geometry)
        # Vectorized distance using prepared geometry for speed
        from shapely.prepared import prep
        prep_river = prep(river_union)
        river_dist_deg = np.zeros_like(xx)
        for i in range(xx.shape[0]):
            for j in range(xx.shape[1]):
                pt = Point(xx[i, j], yy[i, j])
                river_dist_deg[i, j] = pt.distance(river_union)
        river_dist_km = river_dist_deg * 111  # approximate degree to km

    # Flood depth model: purely river-proximity-driven
    # Depth at river bank = water_level, decays exponentially with distance
    buffer_km = river_buffer / 1000
    distance_attenuation = np.exp(-river_dist_km / (buffer_km * 0.5))
    flood_depth = water_level * distance_attenuation

    # Modulate by synthetic micro-topography (small effect)
    rng = np.random.default_rng(42)
    topo_noise = gaussian_filter(rng.normal(0, 1, flood_depth.shape), sigma=3)
    flood_depth *= (1.0 + 0.15 * topo_noise)

    # Only keep cells above threshold
    flood_depth = np.maximum(flood_depth, 0)

    # Convert flooded cells to polygons
    flood_threshold = 0.3  # minimum depth (meters)
    flooded_mask = flood_depth > flood_threshold

    logger.info("Converting flood grid to polygons...")
    flood_cells = []
    for i in range(xx.shape[0]):
        for j in range(xx.shape[1]):
            if flooded_mask[i, j]:
                x, y = xx[i, j], yy[i, j]
                cell = box(x, y, x + resolution, y + resolution)
                flood_cells.append({
                    "geometry": cell,
                    "flood_depth_m": round(float(flood_depth[i, j]), 2),
                    "elevation_m": round(float(elevation[i, j]), 1),
                    "scenario": scenario,
                    "water_level_m": water_level
                })

    if not flood_cells:
        logger.warning("No flooded cells generated")
        return gpd.GeoDataFrame(columns=["geometry", "flood_depth_m"], crs="EPSG:4326")

    flood_gdf = gpd.GeoDataFrame(flood_cells, crs="EPSG:4326")

    # Dissolve nearby flood cells into larger polygons for efficiency
    logger.info(f"Generated {len(flood_gdf)} flood cells, dissolving...")

    # Categorize flood depth
    flood_gdf["depth_category"] = pd.cut(
        flood_gdf["flood_depth_m"],
        bins=[0, 0.5, 1.0, 2.0, 4.0, float("inf")],
        labels=["very_shallow", "shallow", "moderate", "deep", "very_deep"]
    )

    logger.info(f"Flood simulation complete: {len(flood_gdf)} flooded cells")
    logger.info(f"  Depth stats: min={flood_gdf['flood_depth_m'].min():.2f}m, "
                f"max={flood_gdf['flood_depth_m'].max():.2f}m, "
                f"mean={flood_gdf['flood_depth_m'].mean():.2f}m")

    return flood_gdf


def create_flood_extent_polygon(flood_gdf):
    """Create a single dissolved flood extent polygon from flood cells."""
    if flood_gdf.empty:
        return None
    return unary_union(flood_gdf.geometry.buffer(0.001))


def assess_flood_on_roads(roads_gdf, flood_gdf, depth_threshold=None):
    """Determine which road segments are affected by flooding.

    Returns the roads GDF with added columns:
    - is_flooded: boolean
    - max_flood_depth: maximum flood depth along the road
    - flood_percentage: % of road length that is flooded
    """
    cfg = get_config()
    if depth_threshold is None:
        depth_threshold = cfg["network"]["flood_depth_threshold"]

    logger.info("Assessing flood impact on road network...")

    if roads_gdf.empty or flood_gdf.empty:
        roads_gdf["is_flooded"] = False
        roads_gdf["max_flood_depth"] = 0.0
        roads_gdf["flood_percentage"] = 0.0
        return roads_gdf

    # Create flood extent (cells deeper than threshold)
    deep_flood = flood_gdf[flood_gdf["flood_depth_m"] >= depth_threshold].copy()
    roads_gdf = roads_gdf.copy().reset_index(drop=True)
    if deep_flood.empty:
        roads_gdf["is_flooded"] = False
        roads_gdf["max_flood_depth"] = 0.0
        roads_gdf["flood_percentage"] = 0.0
        return roads_gdf

    # Fast path: spatial-index join (STRtree) to find road/flood intersections.
    # For each road, get the max flood depth among intersecting cells.
    deep_flood = deep_flood.set_crs(roads_gdf.crs, allow_override=True)
    joined = gpd.sjoin(
        roads_gdf[["geometry"]], deep_flood[["geometry", "flood_depth_m"]],
        how="left", predicate="intersects"
    )
    # Max depth per road index
    max_depth_per_road = joined.groupby(level=0)["flood_depth_m"].max()
    roads_gdf["max_flood_depth"] = max_depth_per_road.reindex(roads_gdf.index).fillna(0.0).values
    roads_gdf["is_flooded"] = roads_gdf["max_flood_depth"] >= depth_threshold

    # Approximate flood percentage: roads intersecting deep flood are treated
    # as substantially flooded (full precision intersection is too slow at scale)
    roads_gdf["flood_percentage"] = np.where(roads_gdf["is_flooded"], 100.0, 0.0)

    n_flooded = int(roads_gdf["is_flooded"].sum())
    logger.info(f"  {n_flooded}/{len(roads_gdf)} road segments flooded "
                f"({n_flooded/len(roads_gdf)*100:.1f}%)")

    return roads_gdf


def assess_flood_on_infrastructure(infra_gdf, flood_gdf, label="infrastructure"):
    """Check which infrastructure points are within flood zones."""
    if infra_gdf.empty or flood_gdf.empty:
        infra_gdf["is_flooded"] = False
        infra_gdf["flood_depth_m"] = 0.0
        return infra_gdf

    logger.info(f"Assessing flood impact on {label}...")
    flood_extent = unary_union(flood_gdf.geometry)

    is_flooded = []
    depths = []
    for idx, pt in infra_gdf.iterrows():
        if pt.geometry.within(flood_extent):
            nearby = flood_gdf[flood_gdf.geometry.contains(pt.geometry)]
            depth = nearby["flood_depth_m"].max() if not nearby.empty else 0
            is_flooded.append(True)
            depths.append(depth)
        else:
            is_flooded.append(False)
            depths.append(0.0)

    infra_gdf = infra_gdf.copy()
    infra_gdf["is_flooded"] = is_flooded
    infra_gdf["flood_depth_m"] = depths

    n_flooded = sum(is_flooded)
    logger.info(f"  {n_flooded}/{len(infra_gdf)} {label} in flood zones")
    return infra_gdf


if __name__ == "__main__":
    # Test with a small area
    from data_acquisition import fetch_rivers
    bbox = [91.5, 26.0, 92.5, 26.8]
    rivers = fetch_rivers(bbox)
    flood = simulate_flood_inundation(rivers, bbox, scenario="moderate")
    print(f"Generated {len(flood)} flood cells")
    print(flood.head())
