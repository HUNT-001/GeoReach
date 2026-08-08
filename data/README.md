# GeoReach — Data Documentation

This document explains **exactly what data GeoReach uses, where each piece comes
from, and how it was extracted**. It is written so that anyone can reproduce the
dataset from scratch using only free, public sources.

> **Data-integrity statement.** Every input file in `data/raw/` is **real,
> open-source data** — Sentinel-1 SAR, SRTM terrain, and OpenStreetMap (plus an
> optional Census 2011 file). There are **no simulated data files** in the
> repository. There is also **no synthetic flood and no synthetic terrain** in
> the live pipeline: a synthetic fallback exists in the code
> (`flood_simulation.py`, `build_real_data.py`) purely as an offline safety net,
> and it is **never invoked** when the real files are present. The only
> *estimated* values are village populations that OpenStreetMap does not tag —
> and those are derived from **real Census 2011 district figures**, not invented
> (see [Populations](#5-populations)).

---

## 1. Study area

Three flood-prone districts on the north bank of the Brahmaputra, Assam:
**Dhemaji, Lakhimpur, and Majuli** (the world's largest river island).

- Bounding box (extraction): `west 93.6, south 26.65, east 95.3, north 27.85`
- Final analysis is **clipped to the exact district boundaries** (see §4), so no
  neighbouring-state data leaks in.

---

## 2. Data inventory

| File (`data/raw/`) | What it is | Source | Type |
|---|---|---|---|
| `sar_flood_extent.geojson` | Observed flood water extent (21,226 polygons) | **Sentinel-1 SAR** via Google Earth Engine | Real / observed |
| `srtm_dem.tif` | 30 m elevation model (terrain) | **NASA SRTM** via Google Earth Engine | Real / observed |
| `osm_roads.geojson` | Road network (39,026 segments) | **OpenStreetMap** (Overpass API) | Real |
| `osm_settlements.geojson` | Villages & towns (435) | OpenStreetMap | Real |
| `osm_hospitals.geojson` | Hospitals & health facilities (210) | OpenStreetMap | Real |
| `osm_rivers.geojson` | Waterways (448) | OpenStreetMap | Real |
| `osm_bridges.geojson` | Bridges (2,125) | OpenStreetMap | Real |
| `admin_boundaries.geojson` | Dhemaji / Lakhimpur / Majuli polygons | **OSM Nominatim** | Real |
| `census_pca_assam.csv` *(optional)* | Village-level populations | **Census of India 2011** (PCA) | Real |

After clipping to the three districts the analysis works on: **9,090 roads, 80
named settlements, 33 health facilities, 294 waterways, 893 bridges.**

---

## 3. How the satellite data was extracted (Google Earth Engine)

Both satellite layers are exported with the scripts in the **repository root**:
[`sentinel1_flood_gee.js`](../sentinel1_flood_gee.js) and
[`dem_export_gee.js`](../dem_export_gee.js). Paste each into
<https://code.earthengine.google.com>, run it, and export the result to Google
Drive. (Full walkthrough: [`../SENTINEL1_FLOOD_GUIDE.md`](../SENTINEL1_FLOOD_GUIDE.md).)

### 3a. Sentinel-1 flood extent — `sentinel1_flood_gee.js`

- **Collection:** `COPERNICUS/S1_GRD` (Ground Range Detected, C-band SAR)
- **Polarisation / mode:** VV, IW; single orbit (`DESCENDING`)
- **Dry baseline:** median of `2026-04-01 → 2026-05-15`
- **Flood image:** median of `2026-07-15 → 2026-07-31` (2026 monsoon peak)
- **Method — change detection:** new open water appears as a sharp **backscatter
  drop**. A pixel is flagged as *new flood* when the flood image is below
  `−15 dB` **and** dropped `≥ 3 dB` from the baseline.
- **Permanent-water exclusion:** pixels that were already water before the flood,
  plus the `JRC/GSW1_4/GlobalSurfaceWater` permanent-water mask, are removed — so
  the output is **newly inundated land only**, not the perennial Brahmaputra.
- **Speckle handling:** focal-median smoothing; tiny patches (< ~0.5 ha) dropped.
- **Output:** `reduceToVectors` → `sar_flood_extent.geojson`.

*Why SAR?* Radar sees through monsoon cloud, so it works during the flood when
optical satellites (Sentinel-2, Landsat) are blocked.

### 3b. Terrain / DEM — `dem_export_gee.js`

- **Source:** `USGS/SRTMGL1_003` (SRTM 30 m, void-filled)
- **Output:** `srtm_dem.tif`, clipped to the study bbox, EPSG:4326
- **Use:** flood **depth** is derived from terrain — for each observed flood
  polygon we take the elevation of the surrounding dry edge as the local water
  level and subtract ground elevation (a light Height-Above-Nearest-Drainage
  approach). This replaces any flat/assumed depth with real values (0.2–12 m).

---

## 4. How the OpenStreetMap data was extracted

Script: [`../fetch_data_local.py`](../fetch_data_local.py) (run on any machine
with internet — it needs no API key).

1. **Roads / settlements / hospitals / rivers / bridges** — one Overpass API
   query per layer over the study bbox, e.g. roads use
   `way["highway"~"primary|secondary|tertiary|trunk|residential|unclassified"]`.
   Results are converted to GeoJSON in `data/raw/osm_*.geojson`. The script
   retries across mirrors and skips layers already downloaded.
2. **District boundaries** — `python fetch_data_local.py --boundaries` queries
   **OSM Nominatim** for "Dhemaji / Lakhimpur / Majuli district, Assam" and saves
   the returned polygons to `admin_boundaries.geojson`.
3. **Clipping & enrichment** (in `src/integrate_osm.py`):
   - every layer is **clipped to the union of the three district polygons**;
   - each settlement/hospital is assigned its district by **polygon containment**
     (not nearest-guess), so Arunachal/Nagaland places are removed;
   - roads get speed limits and travel times; the road graph is built
     **junction-aware** so intersecting ways actually connect.

Command summary:

```bash
python fetch_data_local.py               # OSM + tries DEM
python fetch_data_local.py --boundaries  # district polygons (do this too)
```

---

## 5. Populations

- **Towns and known settlements** use **real Census of India 2011** figures
  (e.g. North Lakhimpur 105,376 — verified).
- **Villages that OpenStreetMap does not tag with a population** get a
  **district-aware estimate**, computed from *real* Census 2011 numbers as
  `(district population − town population) ÷ number of villages` for that
  district — i.e. the real average rural village size, not an arbitrary constant.
- **Exact village populations (optional):** drop the official **Census 2011
  village PCA** for Assam into `data/raw/census_pca_assam.csv` (columns:
  `Name, District, TRU, TOT_P`). `src/census_join.py` then fuzzy-matches each
  settlement to its Census record and replaces the estimate with the real figure.

So population is **real where available and Census-derived otherwise** — never
fabricated.

---

## 6. What is real vs. derived vs. reference (full transparency)

| Layer | Status |
|---|---|
| Flood extent | **Real** — Sentinel-1 observation, 2026 monsoon dates |
| Flood depth | **Derived** from real SRTM terrain + observed flood extent |
| Terrain (DEM) | **Real** — SRTM 30 m |
| Roads, hospitals, bridges, rivers | **Real** — OpenStreetMap |
| District boundaries | **Real** — OSM Nominatim |
| Town populations | **Real** — Census 2011 |
| Village populations (untagged) | **Derived** from real Census district density |
| Isolation, priority, staging, allocation | **Computed** by the pipeline from the above |

The numbers that appear in the dashboard and report (49 villages cut off, 213
bridges down, 106,850 people without care, etc.) come **only** from the real +
derived layers above.

---

## 7. Folder structure

```
data/
├── raw/            # inputs exactly as downloaded (OSM, SAR, DEM, boundaries, CSVs)
├── processed/      # clipped + assessed GeoJSON produced by the pipeline
│   ├── roads_assessed.geojson
│   ├── settlements_scored.geojson
│   ├── hospitals_assessed.geojson
│   ├── flood_high.geojson
│   └── relief_staging_points.geojson
└── output/         # final deliverables
    ├── georreach_dashboard.html
    ├── priority_report.txt
    ├── analysis_summary.json
    ├── relief_action_plan.csv
    ├── relief_allocation_plan.csv
    ├── situation_report.pdf
    └── care_surface.png
```

---

## 8. Reproduce the dataset in 4 steps

```bash
# 1. OpenStreetMap layers + district boundaries
python fetch_data_local.py
python fetch_data_local.py --boundaries

# 2. Sentinel-1 flood  → run sentinel1_flood_gee.js in Earth Engine,
#    download sar_flood_extent.geojson into data/raw/

# 3. SRTM terrain      → run dem_export_gee.js in Earth Engine,
#    download srtm_dem.tif into data/raw/

# 4. Build everything
cd src && python main.py --scenario high
```

Every input above is free and public; no licensed or private data is required.
