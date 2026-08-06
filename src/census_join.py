"""
Census 2011 Population Join for GeoReach
========================================
Replaces estimated settlement populations with REAL figures from the
Census of India 2011 Primary Census Abstract (PCA), village/town level.

How to get the data (one-time, on your machine):
  1. Go to https://censusindia.gov.in/census.website/data/census-tables
     (or the PCA download: "PCA - Primary Census Abstract").
  2. Download the Assam village-level PCA (state code 18) as CSV/XLSX.
  3. Save it to  data/raw/census_pca_assam.csv
  4. Re-run the pipeline — populations are joined automatically.

Expected columns (standard PCA layout; the loader is tolerant of case and
minor name variants):
  - Name / Area Name         -> village/town name
  - District / District Name -> district
  - TRU                      -> "Rural"/"Urban" (optional)
  - TOT_P / Total Population  -> total population

Matching strategy:
  - Restrict candidate matches to the SAME district (from the clipped OSM data).
  - Normalise names (lowercase, strip suffixes like 'gaon', 'pathar', 'no.').
  - Exact normalised match first, then close fuzzy match (difflib).
"""
import os
import re
import logging
import difflib
import pandas as pd

logger = logging.getLogger("CensusJoin")

# Real district figures (Census 2011) used for smarter fallback defaults.
# village_default ≈ mean rural village population in that district.
DISTRICT_STATS = {
    # district: (total_pop, num_villages, town_pop_est)
    "Dhemaji":   (686133, 1319, 90000),
    "Lakhimpur": (1042137, 1184, 160000),
    "Majuli":    (167304, 246, 40000),
}


def _village_default(district):
    st = DISTRICT_STATS.get(district)
    if not st:
        return 900
    total_pop, n_villages, town_pop = st
    rural = max(total_pop - town_pop, 0)
    return int(max(300, round(rural / max(n_villages, 1) / 10) * 10))


def district_default_pops():
    """Return {district: {'village': x, 'hamlet': y}} realistic defaults."""
    out = {}
    for d in DISTRICT_STATS:
        v = _village_default(d)
        out[d] = {"village": v, "hamlet": max(150, v // 3),
                  "isolated_dwelling": 60, "locality": max(120, v // 4)}
    return out


def _norm(name):
    """Normalise a place name for matching."""
    if not isinstance(name, str):
        return ""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    # Drop common Assamese place suffixes/prefixes that vary between sources
    for tok in ["gaon", "goan", "chapori", "chapari", "tiniali"]:
        s = s.replace(tok, " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_census_pca(raw_dir):
    """Load the Assam PCA CSV if present; return a normalised DataFrame or None."""
    candidates = ["census_pca_assam.csv", "census_pca_assam.xlsx",
                  "census_pca.csv", "pca_assam.csv"]
    path = None
    for c in candidates:
        p = os.path.join(raw_dir, c)
        if os.path.exists(p):
            path = p
            break
    if path is None:
        return None

    logger.info(f"  Census PCA found: {os.path.basename(path)}")
    try:
        if path.endswith(".xlsx"):
            df = pd.read_excel(path)
        else:
            df = pd.read_csv(path, dtype=str, low_memory=False)
    except Exception as e:
        logger.warning(f"  Could not read census file: {e}")
        return None

    # Tolerant column detection
    cols = {c.lower().strip(): c for c in df.columns}

    def find(*names):
        for n in names:
            if n in cols:
                return cols[n]
        return None

    name_col = find("name", "area name", "area_name", "village name", "town/village")
    dist_col = find("district", "district name", "district_name", "district code")
    pop_col = find("tot_p", "total population", "totp", "tot_population", "population")
    tru_col = find("tru", "rural/urban", "level")

    if not (name_col and pop_col):
        logger.warning("  Census file missing Name/TOT_P columns — skipping join")
        return None

    out = pd.DataFrame({
        "census_name": df[name_col].astype(str),
        "census_pop": pd.to_numeric(df[pop_col], errors="coerce"),
    })
    out["census_district"] = df[dist_col].astype(str) if dist_col else ""
    out["tru"] = df[tru_col].astype(str).str.lower() if tru_col else ""
    out = out.dropna(subset=["census_pop"])
    # Prefer 'total' rows if a TRU column mixes Total/Rural/Urban
    if "tru" in out.columns and out["tru"].str.contains("total").any():
        out = out[out["tru"].str.contains("total")]
    out["norm"] = out["census_name"].apply(_norm)
    logger.info(f"  Census rows usable: {len(out)}")
    return out


def join_population(settlements_gdf, raw_dir):
    """Overwrite est_population with real Census figures where a match is found.

    Returns (settlements_gdf, n_matched). Falls back silently if no census file.
    """
    census = load_census_pca(raw_dir)
    if census is None:
        return settlements_gdf, 0

    gdf = settlements_gdf.copy()
    gdf["_norm"] = gdf["name"].apply(_norm)

    # Build per-district lookup for exact + fuzzy matching
    matched = 0
    census_by_dist = {d: sub for d, sub in census.groupby(
        census["census_district"].str.lower().str.strip()
    )} if census["census_district"].astype(bool).any() else {}

    all_norms = census["norm"].tolist()
    all_pops = census.set_index("norm")["census_pop"].to_dict()

    new_pops = []
    for _, row in gdf.iterrows():
        n = row["_norm"]
        dist = str(row.get("district", "")).lower().strip()
        pool = census_by_dist.get(dist)
        if pool is not None and len(pool) > 0:
            norms = pool["norm"].tolist()
            pop_map = pool.set_index("norm")["census_pop"].to_dict()
        else:
            norms = all_norms
            pop_map = all_pops

        if not n:
            new_pops.append(row.get("est_population", 0))
            continue

        # Exact normalised match
        if n in pop_map:
            new_pops.append(int(pop_map[n]))
            matched += 1
            continue
        # Fuzzy match (cutoff 0.9 to avoid false positives)
        close = difflib.get_close_matches(n, norms, n=1, cutoff=0.9)
        if close:
            new_pops.append(int(pop_map[close[0]]))
            matched += 1
        else:
            new_pops.append(row.get("est_population", 0))

    gdf["est_population"] = new_pops
    gdf = gdf.drop(columns=["_norm"])
    logger.info(f"  Census populations joined: {matched}/{len(gdf)} settlements matched")
    return gdf, matched
