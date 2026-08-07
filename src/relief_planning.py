"""
Relief Planning Module for GeoReach
===================================
Turns the accessibility analysis into actionable relief intelligence for
PS3 ("Who is cut off — settlements & lifeline infrastructure").

Provides three things:

1. ACCESS-TO-CARE METRICS — how badly people are cut off from healthcare:
   population with no operational hospital reachable within 60 min, total
   population stranded, and the median extra travel time to care caused by the
   flood (pre vs post).

2. LIFELINE INFRASTRUCTURE — flooded bridges (cut lifelines) and boat-launch /
   staging points: for each isolated settlement, the nearest point on the road
   network that RELIEF can still reach, plus the water-gap distance to bridge.

3. RELIEF ACTION TABLE — a per-settlement CSV field teams can act on directly.
"""
import os
import logging
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, LineString
from shapely.ops import unary_union

import networkx as nx
from network_analysis import _build_node_index, find_nearest_node

logger = logging.getLogger("ReliefPlanning")

CARE_THRESHOLD_MIN = 60  # a hospital reachable within 60 min = "has care access"


# ──────────────────────────────────────────────────────────────
# Flooded bridges (lifeline crossings cut)
# ──────────────────────────────────────────────────────────────

def assess_flooded_bridges(bridges_gdf, flood_gdf):
    """Flag bridges whose span intersects the flood extent."""
    if bridges_gdf is None or bridges_gdf.empty or flood_gdf is None or flood_gdf.empty:
        if bridges_gdf is not None and not bridges_gdf.empty:
            bridges_gdf = bridges_gdf.copy()
            bridges_gdf["is_flooded"] = False
        return bridges_gdf, 0
    b = bridges_gdf.copy()
    flood_union = unary_union(flood_gdf.geometry)
    b["is_flooded"] = b.geometry.intersects(flood_union)
    n = int(b["is_flooded"].sum())
    logger.info(f"  Lifeline bridges cut by flood: {n}/{len(b)}")
    return b, n


# ──────────────────────────────────────────────────────────────
# Access-to-care + staging points (one multi-source Dijkstra)
# ──────────────────────────────────────────────────────────────

def compute_care_and_staging(post_graph, settlements_gdf, hospitals_gdf):
    """Enrich settlements with post-flood access-to-care + staging points.

    Adds columns:
      care_time_min     travel time to nearest OPERATIONAL hospital (post-flood)
      has_care_access   care_time_min <= 60 min
      staging_lon/lat   nearest still-reachable road point (relief launch point)
      staging_gap_km    straight-line distance from settlement to that point
    Returns (settlements_gdf, staging_gdf, metrics_dict).
    """
    s = settlements_gdf.copy()
    n = len(s)
    # Defaults
    s["care_time_min"] = np.inf
    s["has_care_access"] = False
    s["staging_lon"] = np.nan
    s["staging_lat"] = np.nan
    s["staging_gap_km"] = np.nan

    if post_graph is None or post_graph.number_of_nodes() == 0 or hospitals_gdf is None or hospitals_gdf.empty:
        return s, _empty_staging(), _care_metrics(s)

    tree, node_ids = _build_node_index(post_graph)

    # Operational hospitals only (not flooded)
    if "is_operational" in hospitals_gdf.columns:
        oper = hospitals_gdf[hospitals_gdf["is_operational"] == True]
    elif "is_flooded" in hospitals_gdf.columns:
        oper = hospitals_gdf[~hospitals_gdf["is_flooded"]]
    else:
        oper = hospitals_gdf
    if oper.empty:
        oper = hospitals_gdf

    hosp_nodes = []
    for _, h in oper.iterrows():
        hn, hd = find_nearest_node(post_graph, h.geometry, tree, node_ids)
        if hn is not None and hd <= 0.05:
            hosp_nodes.append(hn)

    # Travel time from every node to nearest operational hospital
    if hosp_nodes:
        try:
            time_to_care = nx.multi_source_dijkstra_path_length(
                post_graph, set(hosp_nodes), weight="travel_time_min")
        except nx.NetworkXError:
            time_to_care = {}
    else:
        time_to_care = {}

    # KDTree over the reachable-node subset (for staging-point lookup)
    reachable_nodes = [nid for nid in time_to_care.keys()]
    if reachable_nodes:
        coords = np.array([[post_graph.nodes[nid]["x"], post_graph.nodes[nid]["y"]]
                           for nid in reachable_nodes])
        try:
            from scipy.spatial import cKDTree
            reach_tree = cKDTree(coords)
        except ImportError:
            reach_tree = None
    else:
        reach_tree, coords = None, None

    care_time = []
    has_care = []
    st_lon, st_lat, st_gap = [], [], []
    staging_records = []

    for idx, row in s.iterrows():
        onode, odist = find_nearest_node(post_graph, row.geometry, tree, node_ids)
        t = time_to_care.get(onode, np.inf) if onode is not None else np.inf
        care_time.append(round(t, 1) if np.isfinite(t) else np.inf)
        reachable = np.isfinite(t) and t <= CARE_THRESHOLD_MIN
        has_care.append(bool(reachable))

        # Staging point only needed where settlement can't reach care
        if not reachable and reach_tree is not None:
            d, i = reach_tree.query([row.geometry.x, row.geometry.y])
            sx, sy = coords[i]
            gap_km = round(_haversine(row.geometry.x, row.geometry.y, sx, sy), 2)
            st_lon.append(round(float(sx), 5)); st_lat.append(round(float(sy), 5)); st_gap.append(gap_km)
            if row.get("accessibility_status", "") in ("isolated", "critically_isolated"):
                staging_records.append({
                    "geometry": Point(sx, sy),
                    "serves_settlement": row.get("name", "?"),
                    "district": row.get("district", ""),
                    "population": int(row.get("est_population", 0) or 0),
                    "water_gap_km": gap_km,
                    "status": row.get("accessibility_status", ""),
                })
        else:
            st_lon.append(np.nan); st_lat.append(np.nan); st_gap.append(np.nan)

    s["care_time_min"] = care_time
    s["has_care_access"] = has_care
    s["staging_lon"] = st_lon
    s["staging_lat"] = st_lat
    s["staging_gap_km"] = st_gap

    staging_gdf = (gpd.GeoDataFrame(staging_records, crs="EPSG:4326")
                   if staging_records else _empty_staging())
    if not staging_gdf.empty:
        staging_gdf = staging_gdf.sort_values("population", ascending=False).reset_index(drop=True)

    logger.info(f"  Access-to-care computed for {n} settlements; "
                f"{len(staging_gdf)} relief staging points identified")
    return s, staging_gdf, _care_metrics(s)


def _empty_staging():
    return gpd.GeoDataFrame(
        {"serves_settlement": [], "district": [], "population": [],
         "water_gap_km": [], "status": []},
        geometry=[], crs="EPSG:4326")


def _care_metrics(s):
    """Aggregate access-to-care metrics from enriched settlements."""
    pop = s.get("est_population", pd.Series(dtype=float)).fillna(0)
    total_pop = int(pop.sum())

    status = s.get("accessibility_status", pd.Series([""] * len(s)))
    cut_mask = status.isin(["isolated", "critically_isolated"])
    pop_cut_off = int(pop[cut_mask].sum())
    pop_crit = int(pop[status == "critically_isolated"].sum())

    no_care = ~s.get("has_care_access", pd.Series([False] * len(s))).astype(bool)
    pop_no_care = int(pop[no_care].sum())

    # Extra travel time to care caused by the flood (pre vs post), connected only
    extra = None
    if "pre_travel_time" in s.columns:
        pre = pd.to_numeric(s["pre_travel_time"], errors="coerce")
        post = pd.to_numeric(s["care_time_min"], errors="coerce")
        both = (pre > 0) & np.isfinite(post) & (post > 0)
        if both.any():
            diff = (post[both] - pre[both]).clip(lower=0)
            extra = round(float(diff.median()), 1)

    return {
        "total_population": total_pop,
        "population_cut_off": pop_cut_off,
        "population_critically_isolated": pop_crit,
        "population_without_care_60min": pop_no_care,
        "pct_population_without_care": round(pop_no_care / total_pop * 100, 1) if total_pop else 0,
        "median_extra_minutes_to_care": extra if extra is not None else "n/a",
    }


def _haversine(lon1, lat1, lon2, lat2):
    R = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dl = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


# ──────────────────────────────────────────────────────────────
# Relief action table (CSV)
# ──────────────────────────────────────────────────────────────

def build_relief_table(settlements_gdf, output_path):
    """Write a per-settlement relief action CSV for field teams."""
    s = settlements_gdf.copy()
    s["lat"] = s.geometry.y.round(5)
    s["lon"] = s.geometry.x.round(5)

    def care_str(v):
        try:
            return round(float(v), 1) if np.isfinite(float(v)) else "unreachable"
        except (ValueError, TypeError):
            return "unreachable"

    out = pd.DataFrame({
        "priority_rank": s.get("priority_rank", pd.Series(range(1, len(s) + 1))),
        "settlement": s.get("name", ""),
        "district": s.get("district", ""),
        "status": s.get("accessibility_status", ""),
        "population": s.get("est_population", 0),
        "flood_depth_m": s.get("flood_depth_m", 0).round(1) if "flood_depth_m" in s.columns else 0,
        "nearest_care_time_min": s.get("care_time_min", np.inf).apply(care_str),
        "has_care_within_60min": s.get("has_care_access", False),
        "relief_staging_lat": s.get("staging_lat", np.nan),
        "relief_staging_lon": s.get("staging_lon", np.nan),
        "water_gap_km": s.get("staging_gap_km", np.nan),
        "priority_score": s.get("priority_score", np.nan),
        "lat": s["lat"],
        "lon": s["lon"],
    })
    # Sort by priority rank so the most urgent are on top
    if "priority_rank" in out.columns:
        out = out.sort_values("priority_rank")
    out.to_csv(output_path, index=False)
    logger.info(f"  Relief action table saved: {output_path} ({len(out)} rows)")
    return out
