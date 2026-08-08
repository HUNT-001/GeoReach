"""
Optimal Relief Allocation for GeoReach
======================================
Given the relief staging points and the stranded settlements, choose the K
deployment hubs that COVER THE MOST cut-off population — a greedy maximum-
coverage plan. Answers: "if you have K boat teams, where do you send them?"

A hub (staging point) covers every isolated settlement whose own staging point
is within `radius_km` of it (i.e. reachable by boat from that launch area).
"""
import logging
import numpy as np
import pandas as pd
import geopandas as gpd

logger = logging.getLogger("ReliefAllocation")


def _haversine(lon1, lat1, lon2, lat2):
    R = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dl = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def compute_relief_allocation(staging_gdf, settlements_gdf, k=8, radius_km=8):
    """Greedy max-coverage selection of K staging hubs.

    Returns a GeoDataFrame of chosen hubs with the settlements/population each
    covers, ordered by deployment priority (1 = most people reached first).
    """
    if staging_gdf is None or staging_gdf.empty:
        logger.info("  Relief allocation skipped (no staging points)")
        return None

    hubs = staging_gdf.reset_index(drop=True).copy()
    n = len(hubs)
    lons = hubs.geometry.x.values
    lats = hubs.geometry.y.values
    pops = hubs["population"].fillna(0).astype(float).values
    names = hubs["serves_settlement"].astype(str).values
    dists = hubs["district"].astype(str).values if "district" in hubs.columns else [""] * n

    # Coverage sets: hub i covers settlement j if hubs are within radius_km.
    # (Each staging point sits next to one stranded settlement.)
    covers = []
    for i in range(n):
        d = _haversine(lons[i], lats[i], lons, lats)
        covers.append(set(np.where(d <= radius_km)[0].tolist()))

    covered = set()
    chosen = []
    k = min(k, n)
    for _ in range(k):
        best_i, best_gain, best_new = -1, -1, set()
        for i in range(n):
            if i in [c[0] for c in chosen]:
                continue
            new = covers[i] - covered
            gain = pops[list(new)].sum() if new else 0
            if gain > best_gain:
                best_i, best_gain, best_new = i, gain, new
        if best_i < 0 or best_gain <= 0:
            break
        covered |= best_new
        chosen.append((best_i, best_gain, best_new))

    records = []
    for rank, (i, gain, new) in enumerate(chosen, 1):
        served_names = [names[j] for j in sorted(new, key=lambda j: -pops[j])]
        records.append({
            "geometry": hubs.geometry.iloc[i],
            "deployment_priority": rank,
            "hub_near": names[i],
            "district": dists[i],
            "settlements_covered": len(new),
            "population_covered": int(gain),
            "covered_settlements": ", ".join(served_names[:8]) + ("..." if len(served_names) > 8 else ""),
        })

    alloc = gpd.GeoDataFrame(records, crs="EPSG:4326")
    total_cov = int(alloc["population_covered"].sum()) if not alloc.empty else 0
    logger.info(f"  Relief allocation: {len(alloc)} hubs cover ~{total_cov:,} stranded people "
                f"(radius {radius_km} km)")
    return alloc


def save_allocation(alloc_gdf, path):
    """Save the allocation plan as CSV (with hub coordinates)."""
    df = alloc_gdf.copy()
    df["hub_lat"] = df.geometry.y.round(5)
    df["hub_lon"] = df.geometry.x.round(5)
    cols = ["deployment_priority", "hub_near", "district", "settlements_covered",
            "population_covered", "hub_lat", "hub_lon", "covered_settlements"]
    df[cols].to_csv(path, index=False)
    logger.info(f"  Relief allocation plan saved: {path}")
    return df
