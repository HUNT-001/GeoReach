# GeoReach — Sentinel-1 Flood Mapping Guide

How to replace GeoReach's synthetic flood model with a **real, satellite-derived
flood map** for the 2026 Assam floods over **Dhemaji + Lakhimpur + Majuli**.

Two routes are given. **Route A (Google Earth Engine) is strongly recommended** —
it's free, needs no local processing, and produces a ready-to-use file in minutes.
Route B (Copernicus + SNAP) is the fully-offline alternative.

---

## Why Sentinel-1?

Sentinel-1 carries a C-band Synthetic Aperture Radar (SAR). Radar sees through
clouds and works day or night — essential during a monsoon flood when optical
satellites (Sentinel-2, Landsat) are blocked by cloud. Calm open water reflects
radar away from the sensor, so flooded land appears **dark** (low backscatter).
Comparing a dry "before" image with a "during-flood" image reveals newly
inundated areas as a sharp drop in backscatter.

---

## Study area & dates (already set for you)

| Parameter | Value |
|---|---|
| Bounding box | `[93.6, 26.65, 95.3, 27.85]` (W, S, E, N) |
| Pre-flood window | 2026-04-01 → 2026-05-15 (dry baseline) |
| Flood window | 2026-07-15 → 2026-07-31 (peak) |
| Polarisation | VV |
| Mode | IW (Interferometric Wide) |

The 2026 Assam floods peaked mid-to-late July (75+ dead, ~700,000 displaced,
900+ villages submerged across Dhemaji, Lakhimpur, Majuli and neighbours).

---

## Route A — Google Earth Engine (recommended)

**No downloads of raw scenes, no SNAP, no local GB of data.** GEE holds the entire
Sentinel-1 archive pre-calibrated to dB sigma0.

1. Create a free account at <https://earthengine.google.com> (one-time; approval
   is usually instant for the free non-commercial tier).
2. Open the Code Editor: <https://code.earthengine.google.com>.
3. Open the script **`sentinel1_flood_gee.js`** (in your GeoReach folder), copy
   its entire contents into the editor, and click **Run**.
4. A blue **"New Flood (SAR)"** layer appears over the three districts. Sanity-check
   it against the river system. *If the flood layer looks empty, change*
   `var orbit = 'DESCENDING';` *to* `'ASCENDING'` *and re-run* — one orbit may have
   no acquisition in your window.
5. Go to the **Tasks** tab (right panel) → click **Run** next to
   `georeach_sar_flood_extent` → export to your Google Drive.
6. Download the resulting **`sar_flood_extent.geojson`** from Drive.
7. Put it in `D:\GeoReach\data\raw\` (keep that exact filename).
8. Re-run the pipeline:

   ```
   cd src
   python main.py --scenario high
   ```

GeoReach auto-detects `sar_flood_extent.geojson`, uses the **real** flood extent
(you'll see `Using REAL flood extent data` in the log), and the whole accessibility
analysis is now driven by observed satellite data.

---

## Route B — Copernicus Browser + SNAP (offline)

Use this only if you want to process raw scenes yourself.

### 1. Download the scenes
1. Register at the Copernicus Data Space: <https://dataspace.copernicus.eu>.
2. Open the Browser: <https://browser.dataspace.copernicus.eu>.
3. Draw a box over the three districts (or paste the bbox above).
4. Search **Sentinel-1**, product type **GRD**, mode **IW**.
5. Download **two** scenes covering the AOI:
   - one from **2026-04-01 → 2026-05-15** (pre-flood),
   - one from **2026-07-15 → 2026-07-31** (flood peak).
   Pick scenes from the **same relative orbit / pass direction** for both dates.

### 2. Process each scene in SNAP (ESA's free SAR toolbox)
For **both** the pre-flood and flood scenes, apply this standard chain:

1. **Apply Orbit File** (precise orbits)
2. **Thermal Noise Removal**
3. **Radiometric Calibration** → output **sigma0**
4. **Speckle Filter** (Refined Lee, 5×5)
5. **Range-Doppler Terrain Correction** (geocode to WGS84 / EPSG:4326)
6. **Linear to dB** (convert sigma0 to decibels)
7. Subset to the bbox, keep the **VV** band, and **Export → GeoTIFF**.

Name the outputs so GeoReach can find them:
- pre-flood → `s1_preflood_YYYYMMDD.tif`
- flood    → `s1_flood_YYYYMMDD.tif`

(Any names work as long as the pre-flood file contains `preflood`/`pre_flood`
and the flood file contains `flood` but not `pre`. Avoid the word `test`.)

### 3. Drop them in and run
Place both GeoTIFFs in `D:\GeoReach\data\raw\`, then:

```
cd src
python main.py --scenario high
```

GeoReach's `data_loader.load_sentinel1_flood()` runs the change detection
automatically (see below) and uses the result.

---

## What GeoReach does with the SAR data

`src/data_loader.py → load_sentinel1_flood()` performs the change detection:

1. **Reads** the pre-flood and flood dB rasters (auto-converts if it detects a
   linear/sigma0 scale).
2. **Speckle reduction** — a 3×3 median filter removes salt-and-pepper radar noise.
3. **Change detection** — flags pixels that are **dark during the flood**
   (`VV < −15 dB`) **and** dropped **≥ 3 dB** versus the dry baseline.
4. **Permanent-water exclusion** — pixels that were already water before the flood
   (the Brahmaputra channels) are removed, so only **newly inundated land** is kept.
5. **Denoising** — morphological opening + a minimum-patch-area filter drop
   sub-resolution speckle polygons.
6. **Clipping** — the flood is clipped to your three district boundaries
   (`admin_boundaries.geojson`).
7. **Vectorised** to polygons with a `flood_depth_m` attribute and `scenario =
   "observed_sar"`, matching the schema the rest of the pipeline expects.

The tunable threshold is `threshold_db` (default `−15`). If the map over- or
under-detects water, adjust it by ±1–2 dB.

> **Note on depth:** SAR gives flood *extent*, not *depth*. GeoReach assigns a
> nominal depth so downstream scoring works. To derive real depth, add an SRTM
> DEM (`fetch_data_local.py --dem`) — water depth can then be estimated from the
> terrain within the flooded polygons.

---

## Quick checklist

- [ ] Run `sentinel1_flood_gee.js` in Earth Engine (Route A), **or** process two
      scenes in SNAP (Route B).
- [ ] Get `sar_flood_extent.geojson` (A) or two `.tif` files (B) into `data/raw/`.
- [ ] `python main.py --scenario high`
- [ ] Confirm the log says **"Using REAL flood extent data"**.
- [ ] Open the dashboard — the flood layer is now observed, not simulated.
