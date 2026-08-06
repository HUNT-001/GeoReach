# GeoReach - Geospatial Accessibility Intelligence for Flood Response

A near-real-time geospatial decision support system for identifying settlements and critical infrastructure cut off by flooding in Assam, India.

## Problem Statement

The 2026 Assam floods have disrupted transportation networks, damaged critical infrastructure, and isolated numerous settlements. This system integrates Earth Observation data, GIS, and transportation network analysis to identify **who is cut off**, prioritize affected locations, and support disaster management agencies with actionable intelligence.

## Architecture

```
Earth Observation Data → Flood Inundation Mapping → Transportation Network Analysis
→ Accessibility Assessment → Priority Scoring → Interactive GIS Dashboard
```

### Modules

| Module | File | Purpose |
|--------|------|---------|
| Data Acquisition | `src/data_acquisition.py` | Fetches OSM roads, settlements, hospitals, bridges, rivers |
| Flood Simulation | `src/flood_simulation.py` | Generates flood inundation maps with depth estimates |
| Network Analysis | `src/network_analysis.py` | Builds road graphs, detects disrupted segments |
| Accessibility | `src/accessibility_assessment.py` | Pre/post-flood connectivity comparison |
| Priority Scoring | `src/priority_scoring.py` | Multi-criteria ranking of affected settlements |
| Dashboard | `src/dashboard.py` | Interactive Folium/Leaflet web dashboard |
| Pipeline | `src/main.py` | End-to-end orchestration |

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Run with default settings (moderate flood, subset area)
cd src
python main.py

# Run with specific scenario
python main.py --scenario high

# Run on full Assam (slower)
python main.py --full-area

# Skip data fetching (use cached data)
python main.py --no-fetch
```

## Flood Scenarios

| Scenario | Water Level Rise | Description |
|----------|-----------------|-------------|
| `low` | 2m | Minor flooding, limited road disruption |
| `moderate` | 4m | Significant flooding, multiple road closures |
| `high` | 6m | Severe flooding, widespread isolation |
| `extreme` | 8m | Catastrophic flooding, mass displacement |

## Outputs

- `data/output/georreach_dashboard.html` — Interactive map with all layers
- `data/output/priority_report.txt` — Ranked list of affected settlements
- `data/output/analysis_summary.json` — Machine-readable summary
- `data/processed/` — Intermediate GeoJSON datasets

## Data Sources

- **Roads & Infrastructure**: OpenStreetMap via Overpass API
- **Flood Simulation**: Synthetic DEM + hydrological modeling
- **Population**: Estimated from OSM settlement classification
- **Administrative Boundaries**: OpenStreetMap

## Priority Scoring Criteria

| Factor | Weight | Description |
|--------|--------|-------------|
| Accessibility Loss | 30% | Change in connectivity pre vs post-flood |
| Population Exposure | 25% | Affected population size |
| Infrastructure Criticality | 20% | Loss of hospital/facility access |
| Isolation Severity | 15% | Degree of isolation |
| Flood Depth | 10% | Water depth at settlement |

## Dashboard Features

- Toggle between OpenStreetMap, Satellite, and Dark Mode base maps
- Flood inundation layer with depth-based coloring
- Road network with flood status (green = open, red = blocked)
- Settlement markers color-coded by accessibility status
- Hospital markers with operational status
- Priority restoration corridors
- Summary statistics panel
- Measurement tools and fullscreen mode

## Tech Stack

Python 3.10+ with GeoPandas, NetworkX, Folium, Shapely, SciPy
