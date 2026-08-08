# GeoReach — Geospatial Accessibility Intelligence for Flood Response

**Who is cut off when Assam floods — and who to reach first.**

GeoReach turns a satellite flood observation into an operational relief plan. It
fuses **Sentinel-1 SAR flood extent** with the **OpenStreetMap road network**
across **Dhemaji, Lakhimpur, and Majuli** to work out which villages lose road
access to hospitals, how badly, how many people are stranded, and where to send
help first.

- 🌐 **Live dashboard:** https://hunt-001.github.io/GeoReach/
- 📦 **Repository:** https://github.com/HUNT-001/GeoReach
- 🛰️ **Built entirely on free, public, real data** — see [`data/README.md`](data/README.md)

> **Problem Statement 3 (EO Hackathon · SPARK 3.0):** *Who Is Cut Off — Flood
> Damage & Accessibility Mapping for Settlements and Lifeline Infrastructure.*

---

## Headline results (observed 2026 high-flood scenario)

| Metric | Value |
|---|---|
| Villages cut off | **49 of 80 (61%)** — 6 critically isolated |
| Roads impassable | **2,013 of 9,090 (22%)** |
| Lifeline bridges cut | **213 of 893** |
| People without a hospital reachable in < 60 min | **≈ 106,850 (36%)** |
| Relief staging / boat-launch points identified | **49** |
| Deployment plan | **8 hubs reach ≈ 99,150 stranded people** |
| Hospitals flooded | **0 / 33** (all stay dry — the problem is the roads) |
| End-to-end runtime | **≈ 27 seconds** |

---

## What it does

1. **Maps the real flood** from Sentinel-1 radar (sees through monsoon cloud),
   removing permanent water so only *new* inundation is counted.
2. **Derives flood depth** from SRTM terrain (0.2–12 m), not a flat assumption.
3. **Builds a junction-aware road graph** from OpenStreetMap and marks which
   roads and bridges are underwater.
4. **Classifies every settlement** into 4 tiers — Critically Isolated / Isolated
   / Partially Accessible / Accessible — from remaining routes to hospitals.
5. **Ranks who to reach first** with a transparent multi-criteria priority score.
6. **Plans the response** — access-to-care metrics, boat-launch staging points, a
   max-coverage deployment plan, and a one-click plain-language PDF brief.
7. **Serves it live** in an interactive dashboard, with downloadable CSVs.

## Key features

- **Real satellite flood** (Sentinel-1 SAR + permanent-water exclusion)
- **Terrain-derived flood depth** (SRTM, HAND-style)
- **Junction-aware routing** (fixed graph connectivity: 27,000 → 144 fragments)
- **Access-to-care metrics** — population with no hospital within 60 minutes
- **Lifeline analysis** — cut bridges + relief staging points with water-gap distance
- **Care-access heatmap** — travel-time-to-hospital surface (10/30/60-min bands)
- **Optimal relief allocation** — max-coverage choice of deployment hubs
- **One-click situation report** — plain-language PDF for officials
- **Interactive dashboard** — layers, place search, decision panel, CSV download

---

## Architecture

```
Sentinel-1 SAR + SRTM (Earth Engine)   OpenStreetMap (Overpass) + Nominatim
                 │                                    │
                 └──────────────┬─────────────────────┘
                                ▼
         Clip to districts · junction-aware road graph · flood depth
                                ▼
     Accessibility assessment (4-tier isolation, pre/post-flood routing)
                                ▼
   Priority scoring · access-to-care · staging points · relief allocation
                                ▼
     Interactive dashboard · situation report (PDF) · action-plan CSVs
```

### Modules

| Module | File | Purpose |
|--------|------|---------|
| OSM integration | `src/integrate_osm.py` | Load, clip, enrich real OSM data; assign districts |
| Real-data loader | `src/data_loader.py` | Load SAR flood, SRTM DEM; derive depth; scan inputs |
| Census join | `src/census_join.py` | Real Census 2011 village populations |
| Network analysis | `src/network_analysis.py` | Junction-aware road graph, shortest-path routing |
| Accessibility | `src/accessibility_assessment.py` | 4-tier isolation, connectivity change |
| Priority scoring | `src/priority_scoring.py` | Multi-criteria reach-first ranking |
| Relief planning | `src/relief_planning.py` | Access-to-care, staging points, cut bridges, action CSV |
| Care surface | `src/care_surface.py` | Travel-time-to-hospital heatmap |
| Relief allocation | `src/relief_allocation.py` | Max-coverage deployment plan |
| Situation report | `src/situation_report.py` | Plain-language PDF brief |
| Dashboard | `src/dashboard.py` | Interactive Folium/Leaflet map |
| Pipeline | `src/main.py` | End-to-end orchestration |
| Local fetch | `fetch_data_local.py` | Download OSM + district boundaries |
| Earth Engine | `sentinel1_flood_gee.js`, `dem_export_gee.js` | Export SAR flood + DEM |

---

## Data

All data is **real and public**. Full provenance — what was extracted, from where,
and how — is documented in **[`data/README.md`](data/README.md)**, including the
exact Earth Engine method and parameters. In short: Sentinel-1 flood + SRTM
terrain (via Google Earth Engine), roads/hospitals/bridges/boundaries
(OpenStreetMap), and Census 2011 populations. There is no synthetic flood or
terrain in the live pipeline.

---

## Setup

```bash
pip install -r requirements.txt
```

### Get the data (one-time, free, no API key for OSM)

```bash
# 1. OpenStreetMap layers + real district boundaries
python fetch_data_local.py
python fetch_data_local.py --boundaries

# 2. Sentinel-1 flood: run sentinel1_flood_gee.js at code.earthengine.google.com,
#    download sar_flood_extent.geojson  →  data/raw/

# 3. SRTM terrain: run dem_export_gee.js in Earth Engine,
#    download srtm_dem.tif  →  data/raw/
```

### Run the pipeline

```bash
cd src
python main.py --scenario high      # uses the real downloaded data automatically
```

---

## Outputs (`data/output/`)

| File | What it is |
|---|---|
| `georreach_dashboard.html` | Interactive decision dashboard (all layers) |
| `situation_report.pdf` | One-page plain-language brief for officials |
| `relief_action_plan.csv` | Per-village field action sheet (priority-sorted) |
| `relief_allocation_plan.csv` | Recommended deployment hubs (max-coverage) |
| `priority_report.txt` | Ranked settlement report |
| `analysis_summary.json` | Machine-readable summary of every metric |
| `care_surface.png` | Care-access heatmap image |

## Dashboard features

Flood-depth layer · road status (green open / red blocked) · settlements colour-
coded by isolation tier · hospitals (operational / flooded) · **cut bridges** ·
**relief staging points** · **recommended deployment hubs** · **care-access
heatmap** · restoration corridors · decision panel with live metrics · place-name
search · **one-click CSV download** · fullscreen, measure, and minimap tools.

## Priority scoring criteria

| Factor | Weight |
|--------|--------|
| Accessibility loss (pre vs post-flood) | 30% |
| Population exposure | 25% |
| Infrastructure criticality (hospital access) | 20% |
| Isolation severity | 15% |
| Flood depth | 10% |

---

## Deploy (free)

The dashboard is a single static HTML file — host it free on **GitHub Pages**.
`index.html` at the repo root redirects to the dashboard. A GitHub Actions
workflow (`.github/workflows/deploy.yml`) rebuilds and redeploys on every push.

```bash
git add data/output/ && git commit -m "Update dashboard" && git push
```

---

## Tech stack

Python 3.10+ · GeoPandas · NetworkX · rasterio · SciPy · Shapely · Folium/Leaflet
· Google Earth Engine (Sentinel-1, SRTM) · reportlab · Matplotlib/Pillow

## Team

**Climate Catalyst** — Parthasarathy P · Tanush Pavan Vakkalagadda
Amrita Vishwa Vidyapeetham, Coimbatore
