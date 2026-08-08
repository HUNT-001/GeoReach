"""
Data Loader Module for GeoReach
Loads real datasets (GeoTIFFs, Shapefiles, CSVs) when available,
falls back to OSM/synthetic data when not.
"""
import os
import logging
import glob
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box, Point, Polygon

from config_loader import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _raw_dir():
    cfg = get_config()
    return os.path.join(_project_root(), cfg["paths"]["raw_data"])


# ══════════════════════════════════════════════════════════════════
# DEM Loading
# ══════════════════════════════════════════════════════════════════

def load_dem(dem_path=None):
    """Load a real SRTM or Cartosat DEM GeoTIFF.

    Args:
        dem_path: Path to a single merged DEM GeoTIFF. If None, searches
                  data/raw/ for files matching *dem*.tif or *srtm*.tif.

    Returns:
        dict with keys: data (2D numpy array), transform, crs, bounds
        or None if no DEM found.
    """
    try:
        import rasterio
    except ImportError:
        logger.warning("rasterio not installed — cannot load real DEM. "
                       "Install with: pip install rasterio")
        return None

    if dem_path is None:
        raw = _raw_dir()
        candidates = (
            glob.glob(os.path.join(raw, "*dem*.tif")) +
            glob.glob(os.path.join(raw, "*srtm*.tif")) +
            glob.glob(os.path.join(raw, "*DEM*.tif")) +
            glob.glob(os.path.join(raw, "*SRTM*.tif")) +
            glob.glob(os.path.join(raw, "srtm_tiles", "*.hgt"))
        )
        if not candidates:
            logger.info("No DEM file found in data/raw/. Will use synthetic elevation.")
            return None
        dem_path = candidates[0]
        logger.info(f"Found DEM: {dem_path}")

    with rasterio.open(dem_path) as src:
        data = src.read(1)  # first band
        transform = src.transform
        crs = src.crs
        bounds = src.bounds
        logger.info(f"DEM loaded: {data.shape}, CRS={crs}, "
                    f"bounds=({bounds.left:.2f}, {bounds.bottom:.2f}, "
                    f"{bounds.right:.2f}, {bounds.top:.2f}), "
                    f"elevation range: {np.nanmin(data):.0f}–{np.nanmax(data):.0f}m")

    return {
        "data": data,
        "transform": transform,
        "crs": crs,
        "bounds": bounds,
        "resolution": (transform.a, -transform.e),  # (x_res, y_res) in degrees
    }


def derive_depth_from_dem(flood_gdf, dem=None):
    """Estimate flood DEPTH inside observed flood polygons from terrain.

    Method (a light 'height-above-nearest-drainage' style approach):
      1. Rasterise the flood extent onto the DEM grid.
      2. For every flooded pixel, find the nearest NON-flooded shoreline pixel
         via a Euclidean distance transform; its ground elevation approximates
         the local water-surface elevation (WSE) — water pools up to its edge.
      3. depth = max(0, WSE - ground_elevation), capped at a sane maximum.
      4. Assign each flood polygon the median depth of the pixels it covers
         (sampled at its representative point for speed).

    Returns the flood_gdf with updated 'flood_depth_m' + 'depth_category',
    or the original gdf unchanged if the DEM or dependencies are unavailable.
    """
    if flood_gdf is None or flood_gdf.empty:
        return flood_gdf
    if dem is None:
        dem = load_dem()
    if dem is None:
        return flood_gdf
    try:
        import rasterio
        from rasterio.features import rasterize
        from scipy.ndimage import distance_transform_edt
    except ImportError:
        logger.warning("  rasterio/scipy missing — cannot derive depth from DEM")
        return flood_gdf

    elev = dem["data"].astype(float)
    transform = dem["transform"]
    dem_crs = dem["crs"]
    nrows, ncols = elev.shape

    # Reproject flood to DEM CRS for rasterisation
    fl = flood_gdf.to_crs(dem_crs) if flood_gdf.crs != dem_crs else flood_gdf

    flood_mask = rasterize(
        [(geom, 1) for geom in fl.geometry if geom is not None and not geom.is_empty],
        out_shape=(nrows, ncols), transform=transform, fill=0, dtype="uint8"
    ).astype(bool)

    if not flood_mask.any():
        logger.info("  DEM depth: no overlap between flood and DEM grid")
        return flood_gdf

    # Nearest shoreline (non-flood) pixel for every pixel -> its elevation = WSE
    # distance_transform_edt on the NON-flood array gives, for flooded pixels,
    # indices of the nearest background (non-flood) pixel.
    non_flood = ~flood_mask
    _, (iy, ix) = distance_transform_edt(flood_mask, return_indices=True)
    wse = elev[iy, ix]                       # water-surface elevation grid
    depth = np.where(flood_mask, wse - elev, 0.0)
    depth = np.clip(depth, 0.0, 12.0)        # cap at 12 m
    # Smooth tiny DEM noise
    depth[depth < 0.1] = 0.0

    # Sample depth at each polygon's representative point
    reps = flood_gdf.to_crs(dem_crs).geometry.representative_point()
    inv = ~transform  # world->pixel
    depths = []
    for pt in reps:
        col, row = inv * (pt.x, pt.y)
        r, c = int(row), int(col)
        if 0 <= r < nrows and 0 <= c < ncols and flood_mask[r, c]:
            depths.append(round(float(depth[r, c]), 2))
        else:
            depths.append(None)

    gdf = flood_gdf.copy()
    # Fall back to any existing/nominal depth where sample missed
    existing = gdf["flood_depth_m"] if "flood_depth_m" in gdf.columns else 1.0
    gdf["flood_depth_m"] = [d if d is not None else (
        float(existing.iloc[i]) if hasattr(existing, "iloc") else float(existing)
    ) for i, d in enumerate(depths)]
    # Keep a small floor so genuinely-flooded cells aren't zeroed out
    gdf["flood_depth_m"] = gdf["flood_depth_m"].clip(lower=0.2)
    gdf["depth_category"] = pd.cut(
        gdf["flood_depth_m"], bins=[0, 0.5, 1.0, 2.0, 4.0, float("inf")],
        labels=["very_shallow", "shallow", "moderate", "deep", "very_deep"]
    )
    matched = sum(1 for d in depths if d is not None)
    logger.info(f"  DEM-derived flood depth: {matched}/{len(gdf)} polygons "
                f"(range {gdf['flood_depth_m'].min():.1f}–{gdf['flood_depth_m'].max():.1f} m, "
                f"mean {gdf['flood_depth_m'].mean():.1f} m)")
    return gdf


def merge_dem_tiles(tile_dir, output_path):
    """Merge multiple SRTM .hgt or .tif tiles into a single GeoTIFF.

    Requires GDAL command-line tools (gdal_merge.py or gdalbuildvrt + gdal_translate).
    """
    import subprocess

    tiles = glob.glob(os.path.join(tile_dir, "*.hgt")) + \
            glob.glob(os.path.join(tile_dir, "*.tif"))

    if not tiles:
        logger.error(f"No tiles found in {tile_dir}")
        return None

    logger.info(f"Merging {len(tiles)} DEM tiles...")

    # Try gdal_merge.py first
    try:
        cmd = ["gdal_merge.py", "-o", output_path, "-of", "GTiff"] + tiles
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"Merged DEM saved to {output_path}")
        return output_path
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    # Fallback: gdalbuildvrt + gdal_translate
    try:
        vrt_path = output_path.replace(".tif", ".vrt")
        subprocess.run(["gdalbuildvrt", vrt_path] + tiles, check=True, capture_output=True)
        subprocess.run(["gdal_translate", "-of", "GTiff", vrt_path, output_path],
                       check=True, capture_output=True)
        logger.info(f"Merged DEM saved to {output_path}")
        return output_path
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        logger.error(f"GDAL not available for tile merging: {e}")
        logger.info("Install GDAL or manually merge tiles in QGIS.")
        return None


# ══════════════════════════════════════════════════════════════════
# Sentinel-1 SAR Flood Map Loading
# ══════════════════════════════════════════════════════════════════

def load_sentinel1_flood(preflood_path=None, flood_path=None, threshold_db=-15):
    """Load and process Sentinel-1 SAR data to extract flood extent.

    If pre-processed flood extent GeoTIFF/shapefile is provided, loads directly.
    If raw SAR scenes are provided, computes change detection.

    Args:
        preflood_path: Path to pre-flood SAR GeoTIFF (VV band, calibrated sigma0 in dB)
        flood_path: Path to during-flood SAR GeoTIFF
        threshold_db: Backscatter threshold for water detection (dB)

    Returns:
        GeoDataFrame of flood polygons with depth estimates, or None
    """
    raw = _raw_dir()

    # Check for pre-made flood extent (shapefile or GeoJSON)
    premade = (
        glob.glob(os.path.join(raw, "*flood*extent*.shp")) +
        glob.glob(os.path.join(raw, "*flood*extent*.geojson")) +
        glob.glob(os.path.join(raw, "*flood*map*.shp")) +
        glob.glob(os.path.join(raw, "*flood*map*.geojson")) +
        glob.glob(os.path.join(raw, "*inundation*.shp")) +
        glob.glob(os.path.join(raw, "*inundation*.geojson"))
    )
    if premade:
        logger.info(f"Loading pre-made flood extent: {premade[0]}")
        flood_gdf = gpd.read_file(premade[0])
        if flood_gdf.crs is None:
            flood_gdf = flood_gdf.set_crs("EPSG:4326")
        elif flood_gdf.crs.to_epsg() != 4326:
            flood_gdf = flood_gdf.to_crs("EPSG:4326")

        # Ensure required columns exist
        if "flood_depth_m" not in flood_gdf.columns:
            flood_gdf["flood_depth_m"] = 1.0  # default estimate
        if "depth_category" not in flood_gdf.columns:
            flood_gdf["depth_category"] = pd.cut(
                flood_gdf["flood_depth_m"],
                bins=[0, 0.5, 1.0, 2.0, 4.0, float("inf")],
                labels=["very_shallow", "shallow", "moderate", "deep", "very_deep"]
            )
        if "scenario" not in flood_gdf.columns:
            flood_gdf["scenario"] = "observed"

        # Clip to the 3 districts (declutter neighbouring-state water)
        try:
            bpath = os.path.join(raw, "admin_boundaries.geojson")
            if os.path.exists(bpath):
                bounds = gpd.read_file(bpath)
                if not bounds.empty:
                    union = bounds.to_crs("EPSG:4326").geometry.unary_union
                    before = len(flood_gdf)
                    flood_gdf = flood_gdf[flood_gdf.intersects(union)].copy()
                    logger.info(f"  Clipped observed flood to districts: {before} -> {len(flood_gdf)}")
        except Exception as e:
            logger.warning(f"  Could not clip observed flood: {e}")

        # Flood-exposure buffer. SAR maps OPEN water but under-detects flooding
        # beneath vegetation and in built-up areas (double-bounce appears bright).
        # Buffering the observed water by a modest distance captures the immediate
        # flood-exposure zone where roads/settlements at the water's edge are cut
        # off. Configurable via settings.yaml -> flood.observed_buffer_m (0 = off).
        try:
            buf_m = float(get_config().get("flood", {}).get("observed_buffer_m", 0) or 0)
        except Exception:
            buf_m = 0.0
        if buf_m > 0 and not flood_gdf.empty:
            utm = flood_gdf.to_crs("EPSG:32646")
            utm["geometry"] = utm.geometry.buffer(buf_m)
            flood_gdf = utm.to_crs("EPSG:4326")
            logger.info(f"  Applied {buf_m:.0f} m flood-exposure buffer "
                        f"(accounts for SAR under-detection under vegetation/built-up)")

        # Derive real flood depth from terrain if a DEM is available
        flood_gdf = derive_depth_from_dem(flood_gdf)

        logger.info(f"Flood extent loaded: {len(flood_gdf)} features")
        return flood_gdf

    # Try SAR change detection
    try:
        import rasterio
        from rasterio.features import shapes
        from shapely.geometry import shape
    except ImportError:
        logger.warning("rasterio not installed — cannot process SAR data")
        return None

    # Find SAR files (ignore obvious test/sample scaffolding files)
    def _not_test(p):
        b = os.path.basename(p).lower()
        return "test" not in b and "sample" not in b and "dummy" not in b

    if preflood_path is None:
        candidates = glob.glob(os.path.join(raw, "*preflood*.tif")) + \
                     glob.glob(os.path.join(raw, "*pre_flood*.tif"))
        candidates = [c for c in candidates if _not_test(c)]
        preflood_path = candidates[0] if candidates else None

    if flood_path is None:
        candidates = glob.glob(os.path.join(raw, "*flood*.tif"))
        # Exclude preflood files and test scaffolding
        candidates = [c for c in candidates
                      if "pre" not in os.path.basename(c).lower() and _not_test(c)]
        flood_path = candidates[0] if candidates else None

    if preflood_path is None or flood_path is None:
        logger.info("No Sentinel-1 SAR files found. Will use synthetic flood data.")
        return None

    logger.info(f"Processing SAR change detection:")
    logger.info(f"  Pre-flood: {preflood_path}")
    logger.info(f"  Flood: {flood_path}")

    with rasterio.open(preflood_path) as pre_src:
        pre_data = pre_src.read(1).astype(float)
        transform = pre_src.transform
        crs = pre_src.crs

    with rasterio.open(flood_path) as flood_src:
        flood_data = flood_src.read(1).astype(float)

    # Convert to dB if values suggest linear scale (sigma0)
    if np.nanmean(pre_data[np.isfinite(pre_data) & (pre_data > 0)]) < 1:
        pre_data = 10 * np.log10(np.clip(pre_data, 1e-10, None))
        flood_data = 10 * np.log10(np.clip(flood_data, 1e-10, None))

    # ── Speckle reduction: SAR is noisy; a median filter removes salt-and-pepper
    # speckle so single bright/dark pixels don't create spurious flood specks ──
    try:
        from scipy.ndimage import median_filter, binary_opening
        pre_data = median_filter(pre_data, size=3)
        flood_data = median_filter(flood_data, size=3)
        _have_ndimage = True
    except ImportError:
        _have_ndimage = False

    # Change detection: NEW open water shows a strong backscatter DROP and a
    # low absolute value during flood. Permanent water (rivers) is already dark
    # in the pre-flood image, so we EXCLUDE it — we want newly inundated land.
    change = flood_data - pre_data
    permanent_water = pre_data < threshold_db          # water before the flood
    new_flood = (flood_data < threshold_db) & (change < -3) & (~permanent_water)

    # Morphological opening removes tiny isolated clusters (residual speckle)
    if _have_ndimage:
        new_flood = binary_opening(new_flood, iterations=1)

    flood_mask = new_flood

    # Minimum flood-patch area (m²) to keep — drops sub-resolution noise
    px_area = abs(transform.a * transform.e)  # pixel area in map units^2
    min_area = max(px_area * 5, 0.0)          # >= ~5 pixels

    # Vectorize flood mask
    flood_mask_uint8 = flood_mask.astype(np.uint8)
    flood_polygons = []

    for geom_dict, value in shapes(flood_mask_uint8, transform=transform):
        if value == 1:
            geom = shape(geom_dict)
            if geom.area > min_area:
                # Estimate depth from backscatter change
                # Deeper water → lower backscatter
                centroid = geom.centroid
                col = int((centroid.x - transform.c) / transform.a)
                row = int((centroid.y - transform.f) / transform.e)
                row = np.clip(row, 0, change.shape[0] - 1)
                col = np.clip(col, 0, change.shape[1] - 1)
                depth_est = max(0.1, abs(change[row, col]) / 5)  # rough estimate

                flood_polygons.append({
                    "geometry": geom,
                    "flood_depth_m": round(float(depth_est), 2),
                    "scenario": "observed",
                    "backscatter_change_db": round(float(change[row, col]), 2),
                })

    if not flood_polygons:
        logger.warning("No flood polygons extracted from SAR data")
        return None

    flood_gdf = gpd.GeoDataFrame(flood_polygons, crs=crs)
    if flood_gdf.crs.to_epsg() != 4326:
        flood_gdf = flood_gdf.to_crs("EPSG:4326")

    # Clip to the 3 districts if boundaries were downloaded, else the study bbox
    try:
        bpath = os.path.join(raw, "admin_boundaries.geojson")
        if os.path.exists(bpath):
            bounds = gpd.read_file(bpath)
            if not bounds.empty:
                bounds = bounds.to_crs("EPSG:4326")
                before = len(flood_gdf)
                flood_gdf = flood_gdf[flood_gdf.intersects(bounds.geometry.unary_union)].copy()
                logger.info(f"  Clipped SAR flood to districts: {before} -> {len(flood_gdf)}")
    except Exception as e:
        logger.warning(f"  Could not clip SAR flood to boundaries: {e}")

    flood_gdf["scenario"] = "observed_sar"
    flood_gdf["depth_category"] = pd.cut(
        flood_gdf["flood_depth_m"],
        bins=[0, 0.5, 1.0, 2.0, 4.0, float("inf")],
        labels=["very_shallow", "shallow", "moderate", "deep", "very_deep"]
    )

    # Derive real flood depth from terrain if a DEM is available
    flood_gdf = derive_depth_from_dem(flood_gdf)

    logger.info(f"Extracted {len(flood_gdf)} flood polygons from Sentinel-1 SAR data")
    return flood_gdf


# ══════════════════════════════════════════════════════════════════
# Bhuvan Flood Zones
# ══════════════════════════════════════════════════════════════════

def load_bhuvan_flood_zones():
    """Load ISRO Bhuvan pre-mapped flood hazard zone data."""
    raw = _raw_dir()
    candidates = (
        glob.glob(os.path.join(raw, "*bhuvan*flood*.tif")) +
        glob.glob(os.path.join(raw, "*bhuvan*flood*.shp")) +
        glob.glob(os.path.join(raw, "*flood_zone*.shp")) +
        glob.glob(os.path.join(raw, "*flood_hz*.shp"))
    )
    if not candidates:
        return None

    path = candidates[0]
    logger.info(f"Loading Bhuvan flood zones: {path}")

    if path.endswith((".shp", ".geojson")):
        return gpd.read_file(path).to_crs("EPSG:4326")
    else:
        # Raster flood zone — would need rasterio to vectorize
        logger.info("Bhuvan flood zone raster found — use QGIS to convert to shapefile")
        return None


# ══════════════════════════════════════════════════════════════════
# Administrative Boundaries
# ══════════════════════════════════════════════════════════════════

def load_admin_boundaries():
    """Load administrative boundaries from Bhuvan or other sources."""
    raw = _raw_dir()
    candidates = (
        glob.glob(os.path.join(raw, "bhuvan_admin*", "*.shp")) +
        glob.glob(os.path.join(raw, "*admin*boundary*.shp")) +
        glob.glob(os.path.join(raw, "*district*.shp")) +
        glob.glob(os.path.join(raw, "*admin*.geojson"))
    )
    if not candidates:
        return None

    path = candidates[0]
    logger.info(f"Loading admin boundaries: {path}")
    gdf = gpd.read_file(path)
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


# ══════════════════════════════════════════════════════════════════
# ASDMA Ground Truth
# ══════════════════════════════════════════════════════════════════

def load_asdma_data():
    """Load ASDMA flood damage summary data (CSV).

    Expected columns: district, affected_population, roads_damaged,
    bridges_damaged, relief_camps, crop_area_ha
    """
    raw = _raw_dir()
    candidates = (
        glob.glob(os.path.join(raw, "*asdma*.csv")) +
        glob.glob(os.path.join(raw, "*flood_damage*.csv")) +
        glob.glob(os.path.join(raw, "*damage_report*.csv"))
    )
    if not candidates:
        return None

    path = candidates[0]
    logger.info(f"Loading ASDMA data: {path}")
    df = pd.read_csv(path)
    logger.info(f"  {len(df)} district records loaded")
    return df


# ══════════════════════════════════════════════════════════════════
# Water Level Data
# ══════════════════════════════════════════════════════════════════

def load_water_levels():
    """Load CWC/India-WRIS water level gauge data.

    Expected columns: station, date, water_level_m, danger_level_m
    """
    raw = _raw_dir()
    candidates = (
        glob.glob(os.path.join(raw, "*water_level*.csv")) +
        glob.glob(os.path.join(raw, "*gauge*.csv")) +
        glob.glob(os.path.join(raw, "*cwc*.csv"))
    )
    if not candidates:
        return None

    path = candidates[0]
    logger.info(f"Loading water level data: {path}")
    df = pd.read_csv(path)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    logger.info(f"  {len(df)} water level records loaded")
    return df


# ══════════════════════════════════════════════════════════════════
# Geology / Soil
# ══════════════════════════════════════════════════════════════════

def load_geology():
    """Load Bhukosh GSI geological/soil data."""
    raw = _raw_dir()
    candidates = (
        glob.glob(os.path.join(raw, "*geology*.shp")) +
        glob.glob(os.path.join(raw, "*bhukosh*.shp")) +
        glob.glob(os.path.join(raw, "*soil*.shp")) +
        glob.glob(os.path.join(raw, "*lithology*.shp"))
    )
    if not candidates:
        return None

    path = candidates[0]
    logger.info(f"Loading geology/soil data: {path}")
    gdf = gpd.read_file(path)
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


# ══════════════════════════════════════════════════════════════════
# Master Loader
# ══════════════════════════════════════════════════════════════════

def load_all_real_data():
    """Attempt to load all real datasets. Returns a dict indicating
    what was found and what needs to fall back to synthetic/OSM.

    Returns:
        dict with keys for each dataset type, values are the loaded
        data or None if not available.
    """
    logger.info("=" * 60)
    logger.info("SCANNING FOR REAL DATA")
    logger.info("=" * 60)

    results = {
        "dem": load_dem(),
        "flood_extent": load_sentinel1_flood(),
        "admin_boundaries": load_admin_boundaries(),
        "bhuvan_flood_zones": load_bhuvan_flood_zones(),
        "asdma_data": load_asdma_data(),
        "water_levels": load_water_levels(),
        "geology": load_geology(),
    }

    found = [k for k, v in results.items() if v is not None]
    missing = [k for k, v in results.items() if v is None]

    logger.info(f"\nData scan complete:")
    logger.info(f"  Found: {', '.join(found) if found else 'none'}")
    logger.info(f"  Missing (will use synthetic/OSM): {', '.join(missing)}")

    return results


def get_data_status_report():
    """Generate a human-readable report of what data is available."""
    raw = _raw_dir()
    report = []
    report.append("GeoReach Data Status Report")
    report.append("=" * 40)
    report.append(f"Data directory: {raw}")
    report.append("")

    checks = [
        ("DEM (SRTM/Cartosat)", ["*dem*.tif", "*srtm*.tif", "*DEM*.tif"]),
        ("Sentinel-1 Pre-flood", ["*preflood*.tif", "*pre_flood*.tif"]),
        ("Sentinel-1 Flood", ["*flood*.tif"]),
        ("Flood Extent (Vector)", ["*flood*extent*.shp", "*flood*extent*.geojson",
                                    "*inundation*.shp", "*inundation*.geojson"]),
        ("Admin Boundaries", ["*admin*.shp", "*district*.shp", "*admin*.geojson"]),
        ("Census 2011 PCA (optional)", ["census_pca*.csv", "*pca*.csv"]),
        ("Roads (OSM)", ["osm_roads.geojson", "roads.geojson"]),
        ("Settlements (OSM)", ["osm_settlements.geojson", "settlements.geojson"]),
        ("Hospitals (OSM)", ["osm_hospitals.geojson", "hospitals.geojson"]),
        ("Rivers (OSM)", ["osm_rivers.geojson"]),
        ("Bridges (OSM)", ["osm_bridges.geojson"]),
    ]

    for label, patterns in checks:
        found = False
        for pat in patterns:
            matches = glob.glob(os.path.join(raw, pat))
            if matches:
                report.append(f"  [FOUND]   {label}: {os.path.basename(matches[0])}")
                found = True
                break
        if not found:
            report.append(f"  [MISSING] {label}")

    report.append("")
    return "\n".join(report)


if __name__ == "__main__":
    print(get_data_status_report())
    load_all_real_data()
