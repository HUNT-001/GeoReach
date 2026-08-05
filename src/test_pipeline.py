"""
Test Pipeline - Runs the full GeoReach pipeline with synthetic data
to verify all modules work correctly without network calls.
"""
import os
import sys
import logging
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, LineString, box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_loader import get_config
from flood_simulation import simulate_flood_inundation, assess_flood_on_roads, assess_flood_on_infrastructure
from network_analysis import build_road_graph, analyze_connectivity_change, get_graph_statistics
from accessibility_assessment import run_accessibility_assessment
from priority_scoring import compute_priority_scores, generate_priority_report
from dashboard import build_dashboard

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestPipeline")


def generate_synthetic_data():
    """Generate realistic synthetic data for testing."""
    rng = np.random.default_rng(42)
    bbox = [91.7, 26.1, 92.1, 26.5]  # Small area near Guwahati

    # ── Roads ──
    roads = []
    # Main highway (east-west)
    for i in range(20):
        x1 = bbox[0] + i * (bbox[2] - bbox[0]) / 20
        x2 = bbox[0] + (i + 1) * (bbox[2] - bbox[0]) / 20
        y_center = (bbox[1] + bbox[3]) / 2
        y_jitter = rng.normal(0, 0.01)
        roads.append({
            "geometry": LineString([(x1, y_center + y_jitter), (x2, y_center + y_jitter)]),
            "road_type": "primary" if i % 3 == 0 else "secondary",
            "name": f"NH-37 Segment {i}" if i % 3 == 0 else f"State Road {i}",
            "osm_id": 1000 + i,
            "speed_kmh": 50 if i % 3 == 0 else 35,
        })

    # North-south connecting roads
    for j in range(15):
        x = bbox[0] + j * (bbox[2] - bbox[0]) / 15
        y1 = bbox[1] + rng.uniform(0, 0.1)
        y2 = bbox[3] - rng.uniform(0, 0.1)
        roads.append({
            "geometry": LineString([(x, y1), (x, y2)]),
            "road_type": "tertiary",
            "name": f"District Road {j}",
            "osm_id": 2000 + j,
            "speed_kmh": 25,
        })

    # Village roads
    for k in range(30):
        x1 = rng.uniform(bbox[0], bbox[2])
        y1 = rng.uniform(bbox[1], bbox[3])
        angle = rng.uniform(0, 2 * np.pi)
        length = rng.uniform(0.01, 0.05)
        x2 = x1 + length * np.cos(angle)
        y2 = y1 + length * np.sin(angle)
        roads.append({
            "geometry": LineString([(x1, y1), (x2, y2)]),
            "road_type": "residential",
            "name": "",
            "osm_id": 3000 + k,
            "speed_kmh": 20,
        })

    roads_gdf = gpd.GeoDataFrame(roads, crs="EPSG:4326")
    cfg = get_config()
    roads_gdf["length_m"] = roads_gdf.to_crs(cfg["study_area"]["crs"]).geometry.length
    roads_gdf["travel_time_min"] = (roads_gdf["length_m"] / 1000) / roads_gdf["speed_kmh"] * 60

    # ── Settlements ──
    settlement_types = ["city", "town", "village", "hamlet"]
    pop_estimates = {"city": 100000, "town": 15000, "village": 3000, "hamlet": 500}
    settlements = []
    names = [
        "Guwahati", "Nalbari", "Rangia", "Kamrup", "Hajo",
        "Palasbari", "Sonapur", "Changsari", "Mirza", "Boko",
        "Chaygaon", "Gorchuk", "Azara", "Jalukbari", "Basistha",
        "Kahilipara", "Satgaon", "Narengi", "Jorabat", "Byrnihat",
        "Amingaon", "Sualkuchi", "Baihata", "Kamalpur", "Dharapur"
    ]

    for i in range(25):
        st = settlement_types[min(i // 5, 3)]
        pop = int(pop_estimates[st] * rng.uniform(0.5, 1.5))
        settlements.append({
            "geometry": Point(
                rng.uniform(bbox[0] + 0.02, bbox[2] - 0.02),
                rng.uniform(bbox[1] + 0.02, bbox[3] - 0.02)
            ),
            "name": names[i] if i < len(names) else f"Village_{i}",
            "settlement_type": st,
            "est_population": pop,
            "osm_id": 5000 + i,
        })

    settlements_gdf = gpd.GeoDataFrame(settlements, crs="EPSG:4326")

    # ── Hospitals ──
    hospitals = []
    hosp_names = [
        "GMCH Guwahati", "Dispur Hospital", "Nalbari Civil Hospital",
        "Rangia PHC", "Kamrup District Hospital", "Hajo PHC",
        "Palasbari CHC", "Sonapur PHC"
    ]
    for i in range(8):
        hospitals.append({
            "geometry": Point(
                rng.uniform(bbox[0] + 0.03, bbox[2] - 0.03),
                rng.uniform(bbox[1] + 0.03, bbox[3] - 0.03)
            ),
            "name": hosp_names[i],
            "amenity": "hospital",
            "osm_id": 6000 + i,
        })

    hospitals_gdf = gpd.GeoDataFrame(hospitals, crs="EPSG:4326")

    # ── Rivers ──
    rivers = []
    # Brahmaputra (main river, east-west through center)
    center_y = (bbox[1] + bbox[3]) / 2
    river_points = [(bbox[0] + i * 0.02, center_y + rng.normal(0, 0.02))
                    for i in range(int((bbox[2] - bbox[0]) / 0.02) + 1)]
    rivers.append({
        "geometry": LineString(river_points),
        "name": "Brahmaputra",
        "waterway": "river",
    })
    # Tributary
    trib_points = [(bbox[0] + 0.15, bbox[3] - 0.05),
                   (bbox[0] + 0.18, center_y + 0.05),
                   (bbox[0] + 0.2, center_y)]
    rivers.append({
        "geometry": LineString(trib_points),
        "name": "Tributary",
        "waterway": "river",
    })

    rivers_gdf = gpd.GeoDataFrame(rivers, crs="EPSG:4326")

    return {
        "roads": roads_gdf,
        "settlements": settlements_gdf,
        "hospitals": hospitals_gdf,
        "rivers": rivers_gdf,
        "bbox": bbox,
    }


def test_pipeline():
    """Run the full pipeline with synthetic data."""
    cfg = get_config()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = os.path.join(project_root, cfg["paths"]["output"])
    os.makedirs(output_dir, exist_ok=True)

    logger.info("=" * 60)
    logger.info("GeoReach TEST PIPELINE")
    logger.info("=" * 60)

    # Phase 1: Generate synthetic data
    logger.info("\n--- Phase 1: Generating synthetic data ---")
    data = generate_synthetic_data()
    logger.info(f"Roads: {len(data['roads'])}, Settlements: {len(data['settlements'])}, "
                f"Hospitals: {len(data['hospitals'])}, Rivers: {len(data['rivers'])}")

    # Phase 2: Flood simulation
    logger.info("\n--- Phase 2: Flood simulation ---")
    flood = simulate_flood_inundation(data["rivers"], data["bbox"], scenario="moderate")
    data["flood"] = flood
    logger.info(f"Flood cells: {len(flood)}")

    # Phase 3: Accessibility assessment
    logger.info("\n--- Phase 3: Accessibility assessment ---")
    results = run_accessibility_assessment(data)

    # Phase 4: Priority scoring
    logger.info("\n--- Phase 4: Priority scoring ---")
    results["settlements"] = compute_priority_scores(
        results["settlements"], results["hospitals"]
    )

    # Generate report
    report = generate_priority_report(
        results["settlements"], results["hospitals"], results["summary"]
    )
    report_path = os.path.join(output_dir, "priority_report.txt")
    with open(report_path, "w") as f:
        f.write(report)
    logger.info(f"Report saved: {report_path}")
    print("\n" + report)

    # Phase 5: Dashboard
    logger.info("\n--- Phase 5: Building dashboard ---")
    dashboard_path = os.path.join(output_dir, "georreach_dashboard.html")
    build_dashboard(results, output_path=dashboard_path)

    # Verify outputs
    logger.info("\n--- Verification ---")
    assert os.path.exists(dashboard_path), "Dashboard HTML not created"
    assert os.path.exists(report_path), "Report not created"
    assert "priority_score" in results["settlements"].columns, "Priority scores missing"
    assert "accessibility_status" in results["settlements"].columns, "Accessibility status missing"

    dashboard_size = os.path.getsize(dashboard_path)
    logger.info(f"Dashboard size: {dashboard_size / 1024:.0f} KB")
    logger.info(f"Summary: {results['summary']}")
    logger.info("\nALL TESTS PASSED!")

    return results


if __name__ == "__main__":
    test_pipeline()
