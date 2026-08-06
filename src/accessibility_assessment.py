"""
Accessibility Assessment Module for GeoReach
Comprehensive pre/post-flood accessibility analysis for settlements
and critical infrastructure.
"""
import logging
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import Point

from config_loader import get_config
from network_analysis import (
    build_road_graph, compute_accessibility,
    analyze_connectivity_change, get_graph_statistics
)
from flood_simulation import assess_flood_on_roads, assess_flood_on_infrastructure

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def classify_accessibility(settlements_gdf):
    """Classify settlement accessibility status.

    Categories:
    - accessible: Connected to hospitals, travel time within threshold
    - partially_accessible: Connected but travel time increased significantly
    - isolated: No connection to any hospital
    - critically_isolated: Isolated AND high population
    """
    cfg = get_config()
    max_time = cfg["network"]["max_travel_time"]

    settlements = settlements_gdf.copy()

    conditions = []
    labels = []

    for idx, row in settlements.iterrows():
        post_connected = row.get("post_connected", False)
        pre_connected = row.get("pre_connected", True)
        post_time = row.get("post_travel_time", -1)
        pre_time = row.get("pre_travel_time", -1)
        population = row.get("est_population", 0)

        if not post_connected or post_time < 0:
            if population > 5000:
                labels.append("critically_isolated")
            else:
                labels.append("isolated")
        elif post_time > max_time:
            labels.append("partially_accessible")
        elif pre_time > 0 and post_time > pre_time * 2:
            labels.append("partially_accessible")
        else:
            labels.append("accessible")

    settlements["accessibility_status"] = labels

    # Summary
    status_counts = settlements["accessibility_status"].value_counts()
    logger.info("Accessibility classification:")
    for status, count in status_counts.items():
        logger.info(f"  {status}: {count} settlements")

    return settlements


def assess_infrastructure_accessibility(hospitals_gdf, settlements_gdf, post_graph):
    """Assess how many settlements can reach each hospital after flooding."""
    logger.info("Assessing infrastructure accessibility...")

    if hospitals_gdf.empty:
        return hospitals_gdf

    hospitals = hospitals_gdf.copy()

    # For each hospital, count how many settlements can reach it
    serving_counts = []
    serving_populations = []

    from network_analysis import find_nearest_node, _build_node_index
    import networkx as nx

    # Precompute spatial index + settlement nodes ONCE (fast)
    tree, node_ids = _build_node_index(post_graph)
    sett_nodes = []
    sett_pops = []
    for sidx, settlement in settlements_gdf.iterrows():
        sn, sd = find_nearest_node(post_graph, settlement.geometry, tree, node_ids)
        if sn is not None and sd < 0.05:
            sett_nodes.append(sn)
            sett_pops.append(settlement.get("est_population", 0))

    for hidx, hospital in hospitals.iterrows():
        hosp_node, hosp_dist = find_nearest_node(post_graph, hospital.geometry, tree, node_ids)
        count = 0
        pop = 0

        if hosp_node is not None and hosp_dist < 0.05 and post_graph.number_of_nodes() > 0:
            # One single-source Dijkstra from the hospital (cutoff 60 min),
            # then check which settlement nodes are reachable.
            try:
                reach = nx.single_source_dijkstra_path_length(
                    post_graph, hosp_node, weight="travel_time_min", cutoff=60
                )
            except nx.NetworkXError:
                reach = {}
            for sn, sp in zip(sett_nodes, sett_pops):
                if sn in reach:
                    count += 1
                    pop += sp

        serving_counts.append(count)
        serving_populations.append(pop)

    hospitals["settlements_served"] = serving_counts
    hospitals["population_served"] = serving_populations
    hospitals["is_operational"] = ~hospitals.get("is_flooded", pd.Series(False, index=hospitals.index))

    logger.info(f"  Operational hospitals: {hospitals['is_operational'].sum()}/{len(hospitals)}")
    return hospitals


def identify_emergency_corridors(roads_gdf, settlements_gdf, hospitals_gdf):
    """Identify critical road corridors that, if restored, would reconnect
    the most isolated settlements."""
    logger.info("Identifying emergency restoration corridors...")

    if "is_flooded" not in roads_gdf.columns:
        return []

    flooded_roads = roads_gdf[roads_gdf["is_flooded"]].copy()
    if flooded_roads.empty:
        return []

    isolated = settlements_gdf[
        settlements_gdf.get("accessibility_status", pd.Series()) == "isolated"
    ] if "accessibility_status" in settlements_gdf.columns else settlements_gdf[
        ~settlements_gdf.get("post_connected", pd.Series(True))
    ]

    if isolated.empty:
        return []

    # Only MAJOR flooded roads are meaningful restoration corridors.
    major_types = {"motorway", "trunk", "primary", "secondary", "tertiary"}
    candidates = flooded_roads[flooded_roads["road_type"].isin(major_types)].copy()
    if candidates.empty:
        candidates = flooded_roads.copy()  # fall back to all flooded roads
    candidates = candidates.reset_index(drop=True)

    # Buffer each candidate road by ~5 km and spatial-join isolated settlements.
    # Project to UTM 46N so the buffer distance is correct (metres, not degrees).
    iso = isolated[["geometry", "est_population"]].copy() if "est_population" in isolated.columns else isolated[["geometry"]].copy()
    iso = iso.set_crs(candidates.crs, allow_override=True)

    cand_utm = candidates.to_crs("EPSG:32646")
    iso_utm = iso.to_crs("EPSG:32646")
    cand_buf = cand_utm.copy()
    cand_buf["geometry"] = cand_utm.geometry.buffer(5000)  # 5 km

    joined = gpd.sjoin(cand_buf, iso_utm, how="left", predicate="intersects")
    grp = joined.groupby(level=0)
    near_count = grp["index_right"].count()
    near_pop = grp["est_population"].sum() if "est_population" in joined.columns else near_count * 0

    importance_map = {"motorway": 5, "trunk": 4, "primary": 3, "secondary": 2, "tertiary": 1}
    corridors = []
    for i in range(len(candidates)):
        nearby_isolated = int(near_count.get(i, 0))
        if nearby_isolated <= 0:
            continue
        road = candidates.iloc[i]
        nearby_pop = float(near_pop.get(i, 0) or 0)
        road_importance = importance_map.get(road.get("road_type", ""), 1)
        score = nearby_isolated * 10 + nearby_pop / 100 + road_importance * 5
        corridors.append({
            "road_idx": i,
            "geometry": road.geometry,
            "road_name": road.get("name", "Unnamed"),
            "road_type": road.get("road_type", "unknown"),
            "flood_depth": road.get("max_flood_depth", 0),
            "nearby_isolated_settlements": nearby_isolated,
            "nearby_population": nearby_pop,
            "restoration_priority_score": round(score, 1)
        })

    corridors.sort(key=lambda x: x["restoration_priority_score"], reverse=True)
    logger.info(f"  Identified {len(corridors)} priority restoration corridors")
    return corridors[:20]  # Top 20


def run_accessibility_assessment(data):
    """Run the full accessibility assessment pipeline.

    Args:
        data: dict with keys: roads, settlements, hospitals, rivers, flood, bbox

    Returns:
        dict with all assessment results
    """
    logger.info("=" * 60)
    logger.info("RUNNING ACCESSIBILITY ASSESSMENT")
    logger.info("=" * 60)

    roads = data["roads"]
    settlements = data["settlements"]
    hospitals = data["hospitals"]
    flood = data["flood"]

    # 1. Assess flood impact on roads
    roads_assessed = assess_flood_on_roads(roads, flood)

    # 2. Assess flood impact on infrastructure
    hospitals_assessed = assess_flood_on_infrastructure(hospitals, flood, "hospitals")
    settlements_assessed = assess_flood_on_infrastructure(settlements, flood, "settlements")

    # 3. Build pre-flood and post-flood graphs
    pre_graph = build_road_graph(roads_assessed, use_flood_status=False)
    post_graph = build_road_graph(roads_assessed, use_flood_status=True)

    pre_stats = get_graph_statistics(pre_graph, "Pre-flood")
    post_stats = get_graph_statistics(post_graph, "Post-flood")

    # 4. Connectivity change analysis
    connectivity = analyze_connectivity_change(
        pre_graph, post_graph, settlements_assessed, hospitals_assessed
    )

    # 5. Classify accessibility
    classified = classify_accessibility(connectivity)

    # 6. Infrastructure accessibility
    hospitals_access = assess_infrastructure_accessibility(
        hospitals_assessed, classified, post_graph
    )

    # 7. Emergency corridors
    corridors = identify_emergency_corridors(roads_assessed, classified, hospitals_access)

    results = {
        "roads": roads_assessed,
        "settlements": classified,
        "hospitals": hospitals_access,
        "flood": flood,
        "pre_graph_stats": pre_stats,
        "post_graph_stats": post_stats,
        "emergency_corridors": corridors,
        "summary": {
            "total_settlements": len(classified),
            "isolated": int((classified["accessibility_status"] == "isolated").sum()),
            "critically_isolated": int((classified["accessibility_status"] == "critically_isolated").sum()),
            "partially_accessible": int((classified["accessibility_status"] == "partially_accessible").sum()),
            "accessible": int((classified["accessibility_status"] == "accessible").sum()),
            "flooded_roads": int(roads_assessed["is_flooded"].sum()) if "is_flooded" in roads_assessed.columns else 0,
            "total_roads": len(roads_assessed),
            "flooded_hospitals": int(hospitals_access["is_flooded"].sum()) if "is_flooded" in hospitals_access.columns else 0,
            "total_hospitals": len(hospitals_access),
        }
    }

    logger.info("\n" + "=" * 60)
    logger.info("ASSESSMENT SUMMARY")
    logger.info("=" * 60)
    for k, v in results["summary"].items():
        logger.info(f"  {k}: {v}")

    return results
