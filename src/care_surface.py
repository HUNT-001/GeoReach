"""
Care-Access Heatmap for GeoReach
================================
Builds a travel-time-to-nearest-operational-hospital surface over the study
area (post-flood road network) and renders it as a smooth image overlay for
the dashboard — an isochrone-style "how far is care" map.

Bands: <=10 min (good) -> 10-30 -> 30-60 -> >60 min / unreachable (critical).
"""
import os
import logging
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
from shapely.ops import unary_union

import networkx as nx
from network_analysis import _build_node_index, find_nearest_node

logger = logging.getLogger("CareSurface")

# Isochrone colour ramp (RGBA 0-255). Green = quick care, red = none.
BANDS = [
    (10,  (26, 152, 80)),    # <=10 min
    (30,  (166, 217, 106)),  # 10-30
    (60,  (253, 174, 97)),   # 30-60
    (9e9, (215, 48, 39)),    # >60 / unreachable
]


def _care_time_per_node(post_graph, hospitals_gdf):
    """Travel time from every node to the nearest OPERATIONAL hospital."""
    tree, node_ids = _build_node_index(post_graph)
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
    if not hosp_nodes:
        return tree, node_ids, {}
    try:
        t = nx.multi_source_dijkstra_path_length(post_graph, set(hosp_nodes),
                                                  weight="travel_time_min")
    except nx.NetworkXError:
        t = {}
    return tree, node_ids, t


def build_care_surface(post_graph, hospitals_gdf, boundaries_gdf, bbox,
                       out_png, resolution=0.004):
    """Render the care-access surface to a PNG and return (bounds, stats).

    bounds = [[south, west], [north, east]] for folium ImageOverlay.
    """
    if post_graph is None or post_graph.number_of_nodes() == 0:
        return None, {}

    tree, node_ids, care_time = _care_time_per_node(post_graph, hospitals_gdf)
    if not care_time:
        logger.info("  Care surface skipped (no reachable hospitals)")
        return None, {}

    # Per-node care time array aligned to node_ids (inf where unreachable)
    node_times = np.array([care_time.get(nid, np.inf) for nid in node_ids])

    west, south, east, north = bbox[0], bbox[1], bbox[2], bbox[3]
    xs = np.arange(west, east, resolution)
    ys = np.arange(north, south, -resolution)   # top-down for image rows
    xx, yy = np.meshgrid(xs, ys)
    pts = np.column_stack([xx.ravel(), yy.ravel()])

    # Nearest road node for every grid point (vectorised KDTree query)
    dist, idx = tree.query(pts)
    grid_time = node_times[idx].reshape(xx.shape)
    grid_nodedist = dist.reshape(xx.shape)
    # Cells far from ANY road (>~1.2 km) can't be served -> treat as unreachable
    grid_time = np.where(grid_nodedist > 0.011, np.inf, grid_time)

    # Colour each cell by band -> RGBA image
    h, w = grid_time.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    prev = 0
    for thr, (r, g, b) in BANDS:
        mask = (grid_time > prev) & (grid_time <= thr)
        rgba[mask] = [r, g, b, 150]
        prev = thr
    # care_time == 0 exactly (at a hospital node) -> first band
    rgba[grid_time <= 10] = [26, 152, 80, 150]

    # Mask to the district boundary (transparent outside)
    if boundaries_gdf is not None and not boundaries_gdf.empty:
        union = boundaries_gdf.to_crs("EPSG:4326").geometry.unary_union
        from shapely.prepared import prep
        pu = prep(union)
        inside = np.array([pu.contains(Point(x, y)) for x, y in pts]).reshape(xx.shape)
        rgba[~inside, 3] = 0

    # Save PNG
    try:
        from PIL import Image
        Image.fromarray(rgba, "RGBA").save(out_png)
    except ImportError:
        import matplotlib.pyplot as plt
        plt.imsave(out_png, rgba)
    logger.info(f"  Care-access surface rendered -> {os.path.basename(out_png)} "
                f"({w}x{h} cells)")

    # Stats: share of in-district area within each band
    if boundaries_gdf is not None:
        valid = rgba[..., 3] > 0
        tot = valid.sum()
        def share(lo, hi):
            m = valid & (grid_time > lo) & (grid_time <= hi)
            return round(m.sum() / tot * 100, 1) if tot else 0
        stats = {
            "area_within_10min_pct": share(0, 10),
            "area_10_30min_pct": share(10, 30),
            "area_30_60min_pct": share(30, 60),
            "area_over_60min_pct": share(60, 9e9),
        }
    else:
        stats = {}

    bounds = [[south, west], [north, east]]
    return bounds, stats
