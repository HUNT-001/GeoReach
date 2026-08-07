"""
GeoReach - Main Pipeline Runner
Orchestrates the complete flood accessibility analysis pipeline.

Usage:
    python main.py [--scenario {low,moderate,high,extreme}] [--full-area] [--no-fetch]
"""
import os
import sys
import argparse
import logging
import json
import time

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_loader import get_config
from data_acquisition import run_acquisition, save_dataset
from data_loader import load_all_real_data, get_data_status_report
from flood_simulation import simulate_flood_inundation, assess_flood_on_roads
from accessibility_assessment import run_accessibility_assessment
from priority_scoring import compute_priority_scores, generate_priority_report
from dashboard import build_dashboard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("GeoReach")


def run_pipeline(scenario="moderate", use_subset=True, skip_fetch=False, use_real_data=True):
    """Run the complete GeoReach pipeline.

    Args:
        scenario: Flood scenario ('low', 'moderate', 'high', 'extreme')
        use_subset: Use smaller area for faster processing
        skip_fetch: Skip data fetching (use cached data)
        use_real_data: Build and use geo-accurate real datasets (Dhemaji/Lakhimpur/Majuli)

    Returns:
        dict with all results and output paths
    """
    cfg = get_config()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    start_time = time.time()

    logger.info("=" * 70)
    logger.info("GeoReach - Geospatial Accessibility Intelligence for Flood Response")
    logger.info("=" * 70)
    logger.info(f"Scenario: {scenario}")
    if use_real_data:
        logger.info("Area: Dhemaji + Lakhimpur + Majuli (geo-accurate real data)")
    else:
        logger.info(f"Area: {'Subset (Kamrup/Guwahati)' if use_subset else 'Full Assam'}")
    logger.info("")

    # ── PHASE 1: Data Acquisition ──
    logger.info("PHASE 1: Data Acquisition")
    logger.info("-" * 40)

    raw_dir = os.path.join(project_root, cfg["paths"]["raw_data"])
    processed_dir = os.path.join(project_root, cfg["paths"]["processed_data"])
    output_dir = os.path.join(project_root, cfg["paths"]["output"])
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(processed_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    if use_real_data:
        from integrate_osm import osm_files_present, load_real_osm_datasets
        if osm_files_present(raw_dir):
            # Prefer real OSM data downloaded via fetch_data_local.py
            logger.info("Real OSM data detected — using downloaded OpenStreetMap datasets")
            data = load_real_osm_datasets()
        else:
            # Fall back to geo-accurate hand-built datasets
            logger.info("No OSM downloads found — building geo-accurate datasets from research")
            logger.info("(Run 'python fetch_data_local.py' to download full OSM data)")
            from build_real_data import build_all_datasets
            data = build_all_datasets()
    elif skip_fetch and os.path.exists(os.path.join(raw_dir, "roads.geojson")):
        logger.info("Loading cached data...")
        import geopandas as gpd
        data = {
            "roads": gpd.read_file(os.path.join(raw_dir, "roads.geojson")),
            "settlements": gpd.read_file(os.path.join(raw_dir, "settlements.geojson")),
            "hospitals": gpd.read_file(os.path.join(raw_dir, "hospitals.geojson")),
            "rivers": gpd.read_file(os.path.join(raw_dir, "rivers.geojson")),
        }
        if use_subset:
            data["bbox"] = [91.5, 26.0, 92.5, 26.8]
        else:
            data["bbox"] = cfg["study_area"]["bbox"]

        # Load bridges if available
        bridges_path = os.path.join(raw_dir, "bridges.geojson")
        if os.path.exists(bridges_path):
            data["bridges"] = gpd.read_file(bridges_path)
    else:
        data = run_acquisition(use_subset=use_subset)

    logger.info(f"  Roads: {len(data['roads'])} segments")
    logger.info(f"  Settlements: {len(data['settlements'])} locations")
    logger.info(f"  Hospitals: {len(data['hospitals'])} facilities")
    logger.info("")

    # ── PHASE 1.5: Check for Real Data ──
    logger.info("\nPHASE 1.5: Scanning for Real Data")
    logger.info("-" * 40)
    logger.info(get_data_status_report())

    real_data = load_all_real_data()

    # ── PHASE 2: Flood Inundation ──
    logger.info("\nPHASE 2: Flood Inundation")
    logger.info("-" * 40)

    if real_data.get("flood_extent") is not None:
        flood = real_data["flood_extent"]
        logger.info(f"Using REAL flood extent data: {len(flood)} features")
    else:
        logger.info("No real flood data found — running synthetic simulation")
        flood = simulate_flood_inundation(
            data["rivers"], data["bbox"], scenario=scenario
        )

    data["flood"] = flood
    save_dataset(flood, f"flood_{scenario}", processed_dir)
    logger.info("")

    # ── PHASE 3: Accessibility Assessment ──
    logger.info("PHASE 3: Accessibility Assessment")
    logger.info("-" * 40)

    results = run_accessibility_assessment(data)
    logger.info("")

    # ── PHASE 4: Priority Scoring ──
    logger.info("PHASE 4: Priority Scoring")
    logger.info("-" * 40)

    results["settlements"] = compute_priority_scores(
        results["settlements"], results["hospitals"]
    )
    logger.info("")

    # Save processed data
    save_dataset(results["roads"], "roads_assessed", processed_dir)
    save_dataset(results["settlements"], "settlements_scored", processed_dir)
    save_dataset(results["hospitals"], "hospitals_assessed", processed_dir)
    if results.get("staging_points") is not None and not results["staging_points"].empty:
        save_dataset(results["staging_points"], "relief_staging_points", processed_dir)

    # ── Relief action table (CSV for field teams) ──
    from relief_planning import build_relief_table
    relief_csv = os.path.join(output_dir, "relief_action_plan.csv")
    build_relief_table(results["settlements"], relief_csv)

    # Generate report
    report_text = generate_priority_report(
        results["settlements"], results["hospitals"], results["summary"]
    )
    report_path = os.path.join(output_dir, "priority_report.txt")
    with open(report_path, "w") as f:
        f.write(report_text)
    logger.info(f"Report saved to: {report_path}")

    # Save summary JSON
    summary_path = os.path.join(output_dir, "analysis_summary.json")
    with open(summary_path, "w") as f:
        json.dump(results["summary"], f, indent=2, default=str)

    # ── PHASE 5: Dashboard ──
    logger.info("")
    logger.info("PHASE 5: Interactive Dashboard")
    logger.info("-" * 40)

    dashboard_path = os.path.join(output_dir, "georreach_dashboard.html")
    build_dashboard(results, output_path=dashboard_path)

    elapsed = time.time() - start_time
    logger.info("")
    logger.info("=" * 70)
    logger.info(f"PIPELINE COMPLETE in {elapsed:.1f} seconds")
    logger.info(f"Dashboard: {dashboard_path}")
    logger.info(f"Report: {report_path}")
    logger.info("=" * 70)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="GeoReach - Flood Accessibility Analysis Pipeline"
    )
    parser.add_argument(
        "--scenario", choices=["low", "moderate", "high", "extreme"],
        default="moderate", help="Flood scenario (default: moderate)"
    )
    parser.add_argument(
        "--full-area", action="store_true",
        help="Use full Assam area (slower)"
    )
    parser.add_argument(
        "--no-fetch", action="store_true",
        help="Skip data fetching, use cached data"
    )
    parser.add_argument(
        "--synthetic", action="store_true",
        help="Use synthetic data instead of geo-accurate real data"
    )
    args = parser.parse_args()

    run_pipeline(
        scenario=args.scenario,
        use_subset=not args.full_area,
        skip_fetch=args.no_fetch,
        use_real_data=not args.synthetic,
    )


if __name__ == "__main__":
    main()
