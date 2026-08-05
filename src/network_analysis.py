"""
Transportation Network Analysis Module for GeoReach
Constructs road network graphs, identifies disrupted segments,
and performs pre/post-flood connectivity analysis.
"""
import logging
import networkx as nx
import geopandas as gpd
import numpy as np
from shapely.geometry import Point, LineString
from shapely.ops import nearest_points

from config_loader import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_road_graph(roads_gdf, use_flood_status=False):
    """Build a NetworkX graph from the road network GeoDataFrame.

    Nodes are intersection points, edges are road segments with travel time weights.

    Args:
        roads_gdf: GeoDataFrame with road geometries and attributes
        use_flood_status: If True, exclude flooded roads from the graph

    Returns:
        NetworkX Graph with spatial attributes
    """
    logger.info(f"Building road network graph (flood_filter={use_flood_status})...")

    G = nx.Graph()
    roads = roads_gdf.copy()

    if use_flood_status and "is_flooded" in roads.columns:
        original_count = len(roads)
        roads = roads[~roads["is_flooded"]]
        logger.info(f"  Excluded {original_count - len(roads)} flooded road segments")

    if roads.empty:
        logger.warning("  No roads available for graph construction")
        return G

    def rc(x, y):
        return (round(x, 4), round(y, 4))  # ~11 m snapping

    # ── Pass 1: find junction coordinates (shared by 2+ road features) ──
    # A coordinate is a graph node if it is a road endpoint OR appears in
    # two or more different road features (a true intersection). Interior
    # shape-points that belong to only one road are skipped -> compact graph.
    coord_uses = {}
    geoms = []
    for road in roads.itertuples(index=False):
        geom = getattr(road, "geometry", None)
        if geom is None or geom.is_empty:
            geoms.append(None)
            continue
        coords = list(geom.coords)
        geoms.append(coords if len(coords) >= 2 else None)
        if len(coords) < 2:
            continue
        seen = set()
        for (x, y) in coords:
            key = rc(x, y)
            if key not in seen:
                coord_uses[key] = coord_uses.get(key, 0) + 1
                seen.add(key)

    node_id_map = {}
    node_counter = [0]

    def get_node(x, y):
        key = rc(x, y)
        nid = node_id_map.get(key)
        if nid is None:
            nid = f"n_{node_counter[0]}"
            node_counter[0] += 1
            node_id_map[key] = nid
            G.add_node(nid, x=x, y=y, pos=(x, y))
        return nid

    def is_junction(x, y):
        return coord_uses.get(rc(x, y), 0) >= 2

    # ── Pass 2: build edges between junctions/endpoints ──
    road_records = list(roads.itertuples(index=False))
    for road, coords in zip(road_records, geoms):
        if coords is None:
            continue
        speed_kmh = getattr(road, "speed_kmh", 20) or 20
        road_type = getattr(road, "road_type", "unclassified")
        is_flooded = getattr(road, "is_flooded", False)
        max_depth = getattr(road, "max_flood_depth", 0)
        name = getattr(road, "name", "")
        osm_id = getattr(road, "osm_id", "")

        n = len(coords)
        last_node = get_node(*coords[0])
        acc_len = 0.0
        prev = coords[0]
        for i in range(1, n):
            cur = coords[i]
            dx = (cur[0] - prev[0]) * 111000 * np.cos(np.radians((cur[1] + prev[1]) / 2))
            dy = (cur[1] - prev[1]) * 111000
            acc_len += (dx * dx + dy * dy) ** 0.5
            prev = cur
            # Close an edge at endpoints or intersections
            if i == n - 1 or is_junction(*cur):
                cur_node = get_node(*cur)
                if cur_node != last_node and acc_len > 0:
                    seg_time = (acc_len / 1000) / max(speed_kmh, 5) * 60
                    G.add_edge(last_node, cur_node,
                               length_m=round(acc_len, 1),
                               travel_time_min=round(seg_time, 3),
                               road_type=road_type, name=name, osm_id=osm_id,
                               is_flooded=is_flooded, max_flood_depth=max_depth)
                last_node = cur_node
                acc_len = 0.0

    logger.info(f"  Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    return G


def _build_node_index(G):
    """Build a KDTree over graph node coordinates for fast nearest-node lookup.

    Returns (tree, node_ids) or (None, None) if scipy unavailable / empty graph.
    """
    node_ids = list(G.nodes())
    if not node_ids:
        return None, None
    coords = np.array([[G.nodes[n].get("x", 0), G.nodes[n].get("y", 0)]
                       for n in node_ids])
    try:
        from scipy.spatial import cKDTree
        return cKDTree(coords), node_ids
    except ImportError:
        return None, node_ids


def find_nearest_node(G, point, tree=None, node_ids=None):
    """Find the nearest graph node to a given point.

    Uses a prebuilt KDTree when provided (fast); otherwise linear scan.
    """
    px, py = point.x, point.y
    if tree is not None and node_ids is not None:
        dist, i = tree.query([px, py])
        return node_ids[i], float(dist)

    min_dist = float("inf")
    nearest = None
    for node, data in G.nodes(data=True):
        nx_coord, ny_coord = data.get("x", 0), data.get("y", 0)
        dist = ((px - nx_coord) ** 2 + (py - ny_coord) ** 2) ** 0.5
        if dist < min_dist:
            min_dist = dist
            nearest = node
    return nearest, min_dist


def compute_accessibility(G, origins_gdf, destinations_gdf,
                          weight="travel_time_min", max_travel_time=None):
    """Compute accessibility from origins to destinations via the road network.

    Args:
        G: NetworkX road graph
        origins_gdf: GeoDataFrame of origin points (e.g., settlements)
        destinations_gdf: GeoDataFrame of destination points (e.g., hospitals)
        weight: Edge weight to use for shortest path
        max_travel_time: Maximum travel time threshold (minutes)

    Returns:
        DataFrame with accessibility metrics per origin
    """
    cfg = get_config()
    if max_travel_time is None:
        max_travel_time = cfg["network"]["max_travel_time"]

    logger.info(f"Computing accessibility ({len(origins_gdf)} origins -> "
                f"{len(destinations_gdf)} destinations)...")

    if G.number_of_nodes() == 0:
        logger.warning("  Empty graph, returning no accessibility")
        return origins_gdf.copy()

    # Build spatial index ONCE, and precompute destination nodes ONCE
    tree, node_ids = _build_node_index(G)
    dest_nodes = []
    for didx, dest in destinations_gdf.iterrows():
        dn, dd = find_nearest_node(G, dest.geometry, tree, node_ids)
        if dn is not None and dd <= 0.05:
            dest_nodes.append(dn)

    # KEY OPTIMIZATION: one multi-source Dijkstra FROM all hospital nodes gives,
    # for every node in the graph, the travel time to the NEAREST hospital.
    # This replaces one Dijkstra per settlement (hundreds) with a single pass.
    if dest_nodes:
        try:
            time_to_hospital = nx.multi_source_dijkstra_path_length(
                G, set(dest_nodes), weight=weight
            )
        except nx.NetworkXError:
            time_to_hospital = {}
    else:
        time_to_hospital = {}

    results = []
    for idx, origin in origins_gdf.iterrows():
        origin_node, origin_dist = find_nearest_node(G, origin.geometry, tree, node_ids)

        if origin_node is None or origin_dist > 0.05:
            # Too far from any road
            results.append({
                "origin_idx": idx,
                "nearest_dest_time": float("inf"),
                "nearest_dest_dist": float("inf"),
                "reachable_destinations": 0,
                "is_connected": False,
                "isolation_score": 1.0
            })
            continue

        # Look up this settlement's travel time to the nearest hospital
        min_time = float("inf")
        min_dist = float("inf")
        reachable = 0

        if origin_node in time_to_hospital:
            t = time_to_hospital[origin_node]
            if t <= max_travel_time:
                reachable = 1
                min_time = t
                min_dist = t / 60 * 30  # rough km estimate

        # isolation_score: 0 if reachable within threshold, else scaled by how
        # far over the threshold (capped at 1.0). Disconnected => 1.0
        if reachable:
            isolation_score = 0.0
        else:
            isolation_score = 1.0

        results.append({
            "origin_idx": idx,
            "nearest_dest_time": min_time if min_time != float("inf") else -1,
            "nearest_dest_dist": min_dist if min_dist != float("inf") else -1,
            "reachable_destinations": reachable,
            "is_connected": reachable > 0,
            "isolation_score": round(isolation_score, 3)
        })

    results_df = gpd.GeoDataFrame(results)

    # Merge back with origins
    origins_result = origins_gdf.copy()
    for col in ["nearest_dest_time", "nearest_dest_dist", "reachable_destinations",
                 "is_connected", "isolation_score"]:
        origins_result[col] = [r[col] for r in results]

    connected = sum(1 for r in results if r["is_connected"])
    logger.info(f"  Connected: {connected}/{len(results)} origins")
    logger.info(f"  Fully isolated: {len(results) - connected}/{len(results)} origins")

    return origins_result


def analyze_connectivity_change(pre_graph, post_graph, settlements_gdf, hospitals_gdf):
    """Compare pre-flood and post-flood connectivity.

    Returns settlements with connectivity change metrics.
    """
    logger.info("Analyzing connectivity change (pre-flood vs post-flood)...")

    # Pre-flood accessibility
    pre_access = compute_accessibility(pre_graph, settlements_gdf, hospitals_gdf)
    pre_access = pre_access.rename(columns={
        "nearest_dest_time": "pre_travel_time",
        "reachable_destinations": "pre_reachable",
        "is_connected": "pre_connected",
        "isolation_score": "pre_isolation"
    })

    # Post-flood accessibility
    post_access = compute_accessibility(post_graph, settlements_gdf, hospitals_gdf)
    post_access = post_access.rename(columns={
        "nearest_dest_time": "post_travel_time",
        "reachable_destinations": "post_reachable",
        "is_connected": "post_connected",
        "isolation_score": "post_isolation"
    })

    # Merge results
    result = pre_access.copy()
    for col in ["post_travel_time", "post_reachable", "post_connected", "post_isolation"]:
        if col in post_access.columns:
            result[col] = post_access[col].values

    # Compute change metrics
    result["connectivity_lost"] = result["pre_connected"] & ~result["post_connected"]
    result["newly_isolated"] = ~result["pre_connected"].astype(bool) | result["connectivity_lost"]

    result["travel_time_increase"] = np.where(
        (result["pre_travel_time"] > 0) & (result["post_travel_time"] > 0),
        result["post_travel_time"] - result["pre_travel_time"],
        np.where(result["post_travel_time"] < 0, 999, 0)
    )

    result["accessibility_change"] = np.where(
        result["pre_reachable"] > 0,
        (result["pre_reachable"] - result["post_reachable"]) / result["pre_reachable"],
        np.where(result["post_reachable"] == 0, 1.0, 0.0)
    )

    n_lost = result["connectivity_lost"].sum()
    n_isolated = result["newly_isolated"].sum()
    logger.info(f"  Settlements losing connectivity: {n_lost}")
    logger.info(f"  Total isolated settlements: {n_isolated}")

    return result


def find_alternative_routes(pre_graph, post_graph, origin, destination):
    """Find alternative routes when primary route is disrupted."""
    origin_node_pre, _ = find_nearest_node(pre_graph, origin)
    dest_node_pre, _ = find_nearest_node(pre_graph, destination)
    origin_node_post, _ = find_nearest_node(post_graph, origin)
    dest_node_post, _ = find_nearest_node(post_graph, destination)

    routes = {"pre_flood": None, "post_flood": None}

    # Pre-flood route
    if origin_node_pre and dest_node_pre:
        try:
            path = nx.shortest_path(pre_graph, origin_node_pre, dest_node_pre,
                                    weight="travel_time_min")
            time = nx.shortest_path_length(pre_graph, origin_node_pre, dest_node_pre,
                                           weight="travel_time_min")
            routes["pre_flood"] = {"path": path, "travel_time": time}
        except nx.NetworkXNoPath:
            pass

    # Post-flood route
    if origin_node_post and dest_node_post:
        try:
            path = nx.shortest_path(post_graph, origin_node_post, dest_node_post,
                                    weight="travel_time_min")
            time = nx.shortest_path_length(post_graph, origin_node_post, dest_node_post,
                                           weight="travel_time_min")
            routes["post_flood"] = {"path": path, "travel_time": time}
        except nx.NetworkXNoPath:
            pass

    return routes


def get_graph_statistics(G, label=""):
    """Compute summary statistics for a road network graph."""
    stats = {
        "label": label,
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "connected_components": nx.number_connected_components(G),
        "largest_component_size": len(max(nx.connected_components(G), key=len)) if G.number_of_nodes() > 0 else 0,
    }

    if G.number_of_edges() > 0:
        total_length = sum(d.get("length_m", 0) for _, _, d in G.edges(data=True))
        stats["total_road_length_km"] = round(total_length / 1000, 1)

    logger.info(f"  Graph stats ({label}): {stats['nodes']} nodes, {stats['edges']} edges, "
                f"{stats['connected_components']} components")
    return stats
