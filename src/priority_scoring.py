"""
Priority Scoring Engine for GeoReach
Ranks affected settlements based on multiple criteria for
emergency response prioritization.
"""
import logging
import geopandas as gpd
import pandas as pd
import numpy as np

from config_loader import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def normalize_column(series, higher_is_worse=True):
    """Min-max normalize a series to [0, 1]."""
    s = series.copy().astype(float)
    s = s.replace([np.inf, -np.inf], np.nan)
    s_min = s.min()
    s_max = s.max()
    if s_max == s_min:
        return pd.Series(0.5, index=series.index)
    normalized = (s - s_min) / (s_max - s_min)
    if not higher_is_worse:
        normalized = 1 - normalized
    return normalized.fillna(0.5)


def compute_accessibility_loss_score(settlements):
    """Score based on how much accessibility was lost due to flooding."""
    scores = pd.Series(0.0, index=settlements.index)

    if "accessibility_change" in settlements.columns:
        scores += normalize_column(settlements["accessibility_change"])

    if "connectivity_lost" in settlements.columns:
        scores += settlements["connectivity_lost"].astype(float) * 0.5

    if "post_isolation" in settlements.columns:
        scores += normalize_column(settlements["post_isolation"])

    return scores / scores.max() if scores.max() > 0 else scores


def compute_population_exposure_score(settlements):
    """Score based on exposed population."""
    scores = pd.Series(0.0, index=settlements.index)

    if "est_population" in settlements.columns:
        scores = normalize_column(settlements["est_population"])

    # Bonus for settlements directly in flood zone
    if "is_flooded" in settlements.columns:
        scores += settlements["is_flooded"].astype(float) * 0.3

    return scores / scores.max() if scores.max() > 0 else scores


def compute_infrastructure_criticality_score(settlements, hospitals):
    """Score based on proximity to and loss of critical infrastructure."""
    scores = pd.Series(0.0, index=settlements.index)

    if "reachable_destinations" in settlements.columns and "pre_reachable" in settlements.columns:
        # More facilities lost = higher score
        lost = settlements["pre_reachable"] - settlements.get("post_reachable", 0)
        scores += normalize_column(lost.clip(lower=0))

    # Settlements that lost all hospital access
    if "post_reachable" in settlements.columns:
        no_hospital = (settlements["post_reachable"] == 0).astype(float)
        scores += no_hospital * 0.5

    return scores / scores.max() if scores.max() > 0 else scores


def compute_isolation_severity_score(settlements):
    """Score based on degree of isolation."""
    scores = pd.Series(0.0, index=settlements.index)

    status_scores = {
        "critically_isolated": 1.0,
        "isolated": 0.8,
        "partially_accessible": 0.4,
        "accessible": 0.0
    }

    if "accessibility_status" in settlements.columns:
        scores = settlements["accessibility_status"].map(status_scores).fillna(0.5)

    return scores


def compute_flood_depth_score(settlements):
    """Score based on flood depth at settlement location."""
    if "flood_depth_m" in settlements.columns:
        return normalize_column(settlements["flood_depth_m"])
    return pd.Series(0.0, index=settlements.index)


def compute_priority_scores(settlements, hospitals):
    """Compute composite priority scores for all settlements.

    Returns settlements GDF with priority_score and priority_rank columns.
    """
    cfg = get_config()
    weights = cfg["scoring"]["weights"]

    logger.info("Computing priority scores...")

    settlements = settlements.copy()

    # Compute individual scores
    s_access = compute_accessibility_loss_score(settlements)
    s_pop = compute_population_exposure_score(settlements)
    s_infra = compute_infrastructure_criticality_score(settlements, hospitals)
    s_isolation = compute_isolation_severity_score(settlements)
    s_flood = compute_flood_depth_score(settlements)

    # Weighted composite score
    composite = (
        weights["accessibility_loss"] * s_access +
        weights["population_exposure"] * s_pop +
        weights["infrastructure_criticality"] * s_infra +
        weights["isolation_severity"] * s_isolation +
        weights["flood_depth"] * s_flood
    )

    settlements["score_accessibility"] = s_access.round(3)
    settlements["score_population"] = s_pop.round(3)
    settlements["score_infrastructure"] = s_infra.round(3)
    settlements["score_isolation"] = s_isolation.round(3)
    settlements["score_flood"] = s_flood.round(3)
    settlements["priority_score"] = composite.round(3)

    # Rank (1 = highest priority)
    settlements["priority_rank"] = settlements["priority_score"].rank(
        ascending=False, method="min"
    ).astype(int)

    # Priority category
    settlements["priority_category"] = pd.cut(
        settlements["priority_score"],
        bins=[-0.01, 0.2, 0.4, 0.6, 0.8, 1.01],
        labels=["low", "moderate", "high", "very_high", "critical"]
    )

    # Log summary
    cat_counts = settlements["priority_category"].value_counts()
    logger.info("Priority distribution:")
    for cat in ["critical", "very_high", "high", "moderate", "low"]:
        count = cat_counts.get(cat, 0)
        logger.info(f"  {cat}: {count} settlements")

    # Top 10 most affected
    top10 = settlements.nsmallest(10, "priority_rank")
    logger.info("\nTop 10 priority settlements:")
    for _, row in top10.iterrows():
        name = row.get("name", "Unnamed")
        score = row["priority_score"]
        status = row.get("accessibility_status", "unknown")
        pop = row.get("est_population", 0)
        logger.info(f"  #{row['priority_rank']}: {name} (score={score:.3f}, "
                    f"status={status}, pop={pop})")

    return settlements


def generate_priority_report(settlements, hospitals, summary):
    """Generate a text summary report of priority analysis."""
    report = []
    report.append("=" * 70)
    report.append("GeoReach - FLOOD ACCESSIBILITY PRIORITY REPORT")
    report.append("=" * 70)
    report.append("")

    report.append("OVERVIEW")
    report.append("-" * 40)
    for k, v in summary.items():
        report.append(f"  {k.replace('_', ' ').title()}: {v}")
    report.append("")

    report.append("PRIORITY DISTRIBUTION")
    report.append("-" * 40)
    if "priority_category" in settlements.columns:
        for cat in ["critical", "very_high", "high", "moderate", "low"]:
            count = (settlements["priority_category"] == cat).sum()
            pop = settlements[settlements["priority_category"] == cat].get(
                "est_population", pd.Series(0)
            ).sum()
            report.append(f"  {cat.upper():>12}: {count:>4} settlements | "
                          f"~{int(pop):>8} affected population")
    report.append("")

    report.append("TOP 20 PRIORITY SETTLEMENTS")
    report.append("-" * 40)
    top20 = settlements.nsmallest(20, "priority_rank")
    for _, row in top20.iterrows():
        name = row.get("name", "Unnamed")
        if pd.isna(name) or name == "":
            name = f"Settlement_{row.get('osm_id', 'unknown')}"
        report.append(
            f"  Rank #{row['priority_rank']:>3} | {name:<25} | "
            f"Score: {row['priority_score']:.3f} | "
            f"Status: {row.get('accessibility_status', 'N/A'):<20} | "
            f"Pop: {row.get('est_population', 0):>6}"
        )
    report.append("")

    if not hospitals.empty and "is_flooded" in hospitals.columns:
        report.append("HOSPITAL STATUS")
        report.append("-" * 40)
        for _, h in hospitals.iterrows():
            name = h.get("name", "Unnamed Hospital")
            if pd.isna(name):
                name = "Unnamed Hospital"
            status = "FLOODED" if h.get("is_flooded", False) else "Operational"
            served = h.get("settlements_served", "N/A")
            report.append(f"  {name:<30} | {status:<12} | Serving: {served} settlements")

    report.append("")
    report.append("=" * 70)
    return "\n".join(report)
