"""
Interactive GIS Dashboard for GeoReach
Generates a Folium/Leaflet-based interactive HTML dashboard with
multiple map layers for flood accessibility decision support.
"""
import os
import logging
import folium
from folium import plugins
import geopandas as gpd
import pandas as pd
import numpy as np
import json
import branca.colormap as cm

from config_loader import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_base_map():
    """Create the base Folium map centered on Assam."""
    cfg = get_config()
    center = cfg["output"]["map_center"]
    zoom = cfg["output"]["map_zoom"]

    m = folium.Map(
        location=center,
        zoom_start=zoom,
        tiles=None,
        control_scale=True,
        prefer_canvas=True
    )

    # Add tile layers
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satellite",
    ).add_to(m)
    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        attr="CartoDB",
        name="Dark Mode",
    ).add_to(m)

    return m


def add_flood_layer(m, flood_gdf):
    """Add flood inundation layer with depth-based coloring."""
    if flood_gdf is None or flood_gdf.empty:
        return m

    logger.info("Adding flood inundation layer...")

    flood_group = folium.FeatureGroup(name="Flood Inundation", show=True)

    # Color by depth
    depth_colors = {
        "very_shallow": "#AED8F0",
        "shallow": "#5BA3D9",
        "moderate": "#2171B5",
        "deep": "#08519C",
        "very_deep": "#08306B"
    }

    # Draw flood as ONE dissolved polygon per depth band. Dissolving merges the
    # thousands of overlapping buffered cells into a few clean shapes — far
    # smaller HTML and much smoother panning/zooming for end users.
    fg = flood_gdf.copy()
    bands = [(0, 1, "#AED8F0"), (1, 2, "#5BA3D9"), (2, 3, "#2171B5"),
             (3, 5, "#08519C"), (5, 100, "#08306B")]
    for lo, hi, color in bands:
        sub = fg[(fg["flood_depth_m"] >= lo) & (fg["flood_depth_m"] < hi)]
        if sub.empty:
            continue
        try:
            merged = sub.geometry.union_all() if hasattr(sub.geometry, "union_all") \
                else sub.geometry.unary_union
            merged = merged.simplify(0.0004, preserve_topology=True)  # ~45 m
        except Exception:
            merged = sub.geometry.unary_union
        folium.GeoJson(
            merged.__geo_interface__,
            style_function=lambda x, c=color: {
                "fillColor": c, "color": c, "weight": 0, "fillOpacity": 0.5,
            },
            name=f"Flood {lo}-{hi} m",
        ).add_to(flood_group)

    flood_group.add_to(m)

    # Custom depth-scale legend, pinned bottom-left (above the scale bar) so it
    # never overlaps the summary panel on the right.
    vmax = float(flood_gdf["flood_depth_m"].max())
    legend_html = f"""
    <div style="
        position: fixed; bottom: 42px; left: 10px; z-index: 9999;
        background: rgba(255,255,255,0.95); border-radius: 8px; padding: 8px 10px;
        font-family: 'Segoe UI', Arial, sans-serif; font-size: 11px; color:#1b3a5b;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);">
      <div style="font-weight:600; margin-bottom:4px;">Flood depth (m)</div>
      <div style="width:150px; height:10px; border-radius:3px;
           background: linear-gradient(90deg,#AED8F0,#5BA3D9,#2171B5,#08519C,#08306B);"></div>
      <div style="display:flex; justify-content:space-between; margin-top:2px; color:#555;">
        <span>0</span><span>{vmax/2:.0f}</span><span>{vmax:.0f}+</span>
      </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    return m


def add_road_network_layer(m, roads_gdf):
    """Add road network layer with flood status coloring."""
    if roads_gdf is None or roads_gdf.empty:
        return m

    logger.info("Adding road network layer...")
    cfg = get_config()
    colors = cfg["output"]["color_scheme"]

    # Performance: with tens of thousands of roads, draw them as TWO batched
    # GeoJson layers (blocked vs open) instead of one object per road. Also
    # drop minor OPEN roads (residential/unclassified) to keep the map light;
    # ALL flooded roads are kept since they matter most.
    major = {"motorway", "trunk", "primary", "secondary", "tertiary"}
    rg = roads_gdf.copy()
    if "is_flooded" not in rg.columns:
        rg["is_flooded"] = False

    open_major = rg[(~rg["is_flooded"]) & (rg["road_type"].isin(major))]

    # Blocked roads: keep ALL major blocked; sample minor blocked to keep the
    # HTML light while still conveying the extent of the disruption.
    blocked_all = rg[rg["is_flooded"]]
    blocked_major = blocked_all[blocked_all["road_type"].isin(major)]
    blocked_minor = blocked_all[~blocked_all["road_type"].isin(major)]
    if len(blocked_minor) > 4000:
        blocked_minor = blocked_minor.sample(4000, random_state=42)
    import pandas as _pd
    blocked = _pd.concat([blocked_major, blocked_minor])

    def _fc(gdf):
        # Simplify line geometry (~35 m) to shrink the HTML and smooth rendering
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {},
             "geometry": g.simplify(0.0003, preserve_topology=False).__geo_interface__}
            for g in gdf.geometry if g is not None and not g.is_empty
        ]}

    open_group = folium.FeatureGroup(name="Open Roads (major)", show=True)
    folium.GeoJson(
        _fc(open_major),
        style_function=lambda x: {"color": colors["road_open"], "weight": 2, "opacity": 0.7},
    ).add_to(open_group)
    open_group.add_to(m)

    blocked_group = folium.FeatureGroup(name="Flooded / Blocked Roads", show=True)
    folium.GeoJson(
        _fc(blocked),
        style_function=lambda x: {"color": colors["road_blocked"], "weight": 2.5, "opacity": 0.85},
    ).add_to(blocked_group)
    blocked_group.add_to(m)

    logger.info(f"  Drew {len(open_major)} open-major + {len(blocked)} blocked roads (batched)")
    return m


def add_settlements_layer(m, settlements_gdf):
    """Add settlements layer with priority-based styling."""
    if settlements_gdf is None or settlements_gdf.empty:
        return m

    logger.info("Adding settlements layer...")

    # Separate layers for different statuses
    status_configs = {
        "critically_isolated": {
            "color": "#FF0000", "icon": "exclamation-triangle",
            "prefix": "fa", "group_name": "Critically Isolated Settlements", "show": True
        },
        "isolated": {
            "color": "#FF6600", "icon": "ban",
            "prefix": "fa", "group_name": "Isolated Settlements", "show": True
        },
        "partially_accessible": {
            "color": "#FFAA00", "icon": "exclamation-circle",
            "prefix": "fa", "group_name": "Partially Accessible", "show": True
        },
        "accessible": {
            "color": "#44BB44", "icon": "check-circle",
            "prefix": "fa", "group_name": "Accessible Settlements", "show": False
        },
    }

    for status, config in status_configs.items():
        subset = settlements_gdf[settlements_gdf.get("accessibility_status", "") == status]
        if subset.empty:
            continue

        group = folium.FeatureGroup(name=config["group_name"], show=config["show"])

        for idx, sett in subset.iterrows():
            name = sett.get("name", "Unnamed Settlement")
            if pd.isna(name) or name == "":
                name = f"Settlement {sett.get('osm_id', idx)}"

            popup_html = f"""
            <div style="min-width:200px">
                <h4 style="margin:0 0 5px 0">{name}</h4>
                <table style="font-size:12px">
                    <tr><td><b>Status:</b></td><td>{status.replace('_', ' ').title()}</td></tr>
                    <tr><td><b>Type:</b></td><td>{sett.get('settlement_type', 'N/A')}</td></tr>
                    <tr><td><b>Est. Population:</b></td><td>{sett.get('est_population', 'N/A'):,}</td></tr>
                    <tr><td><b>Priority Rank:</b></td><td>#{sett.get('priority_rank', 'N/A')}</td></tr>
                    <tr><td><b>Priority Score:</b></td><td>{sett.get('priority_score', 'N/A')}</td></tr>
                    <tr><td><b>Flood Depth:</b></td><td>{sett.get('flood_depth_m', 0):.1f}m</td></tr>
                </table>
            </div>
            """

            # Size by population
            pop = sett.get("est_population", 1000)
            radius = max(4, min(15, np.log10(max(pop, 1)) * 3))

            folium.CircleMarker(
                location=[sett.geometry.y, sett.geometry.x],
                radius=radius,
                color=config["color"],
                fill=True,
                fillColor=config["color"],
                fillOpacity=0.7,
                weight=2,
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{name} ({status.replace('_', ' ')})",
            ).add_to(group)

        group.add_to(m)

    return m


def add_hospitals_layer(m, hospitals_gdf):
    """Add hospitals layer with operational status."""
    if hospitals_gdf is None or hospitals_gdf.empty:
        return m

    logger.info("Adding hospitals layer...")
    hosp_group = folium.FeatureGroup(name="Hospitals & Health Facilities", show=True)

    for idx, hosp in hospitals_gdf.iterrows():
        name = hosp.get("name", "Health Facility")
        if pd.isna(name):
            name = "Health Facility"

        is_flooded = hosp.get("is_flooded", False)
        is_operational = hosp.get("is_operational", True)
        color = "red" if is_flooded else "green"
        icon_name = "times-circle" if is_flooded else "hospital-o"

        popup_html = f"""
        <div style="min-width:180px">
            <h4 style="margin:0 0 5px 0">{name}</h4>
            <table style="font-size:12px">
                <tr><td><b>Status:</b></td><td>{'FLOODED' if is_flooded else 'Operational'}</td></tr>
                <tr><td><b>Settlements Served:</b></td><td>{hosp.get('settlements_served', 'N/A')}</td></tr>
                <tr><td><b>Pop. Served:</b></td><td>{hosp.get('population_served', 'N/A'):,}</td></tr>
            </table>
        </div>
        """

        folium.Marker(
            location=[hosp.geometry.y, hosp.geometry.x],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"{name} ({'FLOODED' if is_flooded else 'OK'})",
            icon=folium.Icon(color=color, icon=icon_name, prefix="fa"),
        ).add_to(hosp_group)

    hosp_group.add_to(m)
    return m


def add_emergency_corridors_layer(m, corridors):
    """Add emergency restoration corridor layer."""
    if not corridors:
        return m

    logger.info("Adding emergency corridors layer...")
    corr_group = folium.FeatureGroup(name="Priority Restoration Corridors", show=True)

    for i, corr in enumerate(corridors[:10]):
        popup_html = f"""
        <div style="min-width:200px">
            <h4>Restoration Priority #{i+1}</h4>
            <table style="font-size:12px">
                <tr><td><b>Road:</b></td><td>{corr.get('road_name', 'Unnamed')}</td></tr>
                <tr><td><b>Type:</b></td><td>{corr.get('road_type', 'N/A')}</td></tr>
                <tr><td><b>Flood Depth:</b></td><td>{corr.get('flood_depth', 0):.1f}m</td></tr>
                <tr><td><b>Isolated Nearby:</b></td><td>{corr.get('nearby_isolated_settlements', 0)}</td></tr>
                <tr><td><b>Affected Pop:</b></td><td>{corr.get('nearby_population', 0):,}</td></tr>
                <tr><td><b>Priority Score:</b></td><td>{corr.get('restoration_priority_score', 0):.1f}</td></tr>
            </table>
        </div>
        """

        folium.GeoJson(
            corr["geometry"].__geo_interface__,
            style_function=lambda x: {
                "color": "#FF00FF",
                "weight": 5,
                "dashArray": "10 5",
                "opacity": 0.9,
            },
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"Restoration Priority #{i+1}",
        ).add_to(corr_group)

    corr_group.add_to(m)
    return m


def add_staging_points_layer(m, staging_gdf):
    """Add relief staging / boat-launch points for isolated settlements."""
    if staging_gdf is None or staging_gdf.empty:
        return m
    logger.info("Adding relief staging points layer...")
    grp = folium.FeatureGroup(name="Relief Staging Points", show=True)
    for _, sp in staging_gdf.iterrows():
        popup = folium.Popup(
            f"<b>Relief staging point</b><br>"
            f"Serves: {sp.get('serves_settlement','?')} ({sp.get('district','')})<br>"
            f"Population: {int(sp.get('population',0)):,}<br>"
            f"Water gap to cross: <b>{sp.get('water_gap_km',0):.1f} km</b><br>"
            f"Settlement status: {str(sp.get('status','')).replace('_',' ')}",
            max_width=260)
        folium.Marker(
            location=[sp.geometry.y, sp.geometry.x],
            tooltip=f"Staging → {sp.get('serves_settlement','?')} ({sp.get('water_gap_km',0):.1f} km)",
            popup=popup,
            icon=folium.Icon(color="purple", icon="ship", prefix="fa"),
        ).add_to(grp)
    grp.add_to(m)
    return m


def add_flooded_bridges_layer(m, bridges_gdf):
    """Add flooded/cut lifeline bridges."""
    if bridges_gdf is None or bridges_gdf.empty or "is_flooded" not in bridges_gdf.columns:
        return m
    cut = bridges_gdf[bridges_gdf["is_flooded"]]
    if cut.empty:
        return m
    logger.info("Adding flooded bridges layer...")
    grp = folium.FeatureGroup(name="Cut Bridges (lifeline)", show=False)
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": g.__geo_interface__} for g in cut.geometry
        if g is not None and not g.is_empty]}
    folium.GeoJson(
        fc,
        style_function=lambda x: {"color": "#8B0000", "weight": 5, "opacity": 0.95},
        tooltip="Cut bridge (flooded lifeline crossing)",
    ).add_to(grp)
    grp.add_to(m)
    return m


def add_title_banner(m, scenario="high"):
    """Add a fixed title banner across the top of the map."""
    banner = f"""
    <div style="
        position: fixed; top: 0; left: 0; right: 0; height: 46px;
        background: linear-gradient(90deg,#0d1b2a,#1b3a5b);
        color: #fff; z-index: 10000; display: flex; align-items: center;
        padding: 0 16px; font-family: 'Segoe UI', Arial, sans-serif;
        box-shadow: 0 2px 8px rgba(0,0,0,0.35);">
        <div style="font-size:18px; font-weight:700; letter-spacing:.3px;">
            GeoReach <span style="font-weight:400; opacity:.8;">| Flood Accessibility Intelligence</span>
        </div>
        <div style="margin-left:auto; font-size:12px; opacity:.9; text-align:right;">
            Dhemaji · Lakhimpur · Majuli &nbsp;|&nbsp; Sentinel-1 observed flood ({scenario} scenario)<br>
            <span style="opacity:.7;">Assam floods 2026 · roads, hospitals &amp; villages from OpenStreetMap · SRTM terrain depth</span>
        </div>
    </div>
    <style>
      /* Push Leaflet top controls below the fixed banner */
      .leaflet-top {{ margin-top: 50px; }}
      .leaflet-control-layers, .leaflet-bar {{ box-shadow: 0 1px 5px rgba(0,0,0,0.3); }}
    </style>
    """
    m.get_root().html.add_child(folium.Element(banner))
    return m


def add_summary_panel(m, summary, settlements=None):
    """Add a decision-support panel: key metrics, priority action list, legend."""
    total = summary.get('total_settlements', 0) or 1
    crit = summary.get('critically_isolated', 0)
    iso = summary.get('isolated', 0)
    cut = crit + iso
    pct_cut = round(cut / total * 100)
    roads_pct = round(summary.get('flooded_roads', 0) / max(summary.get('total_roads', 1), 1) * 100)

    # Build top-priority action list (up to 6). Colours match the map markers.
    rows = ""
    try:
        if settlements is not None and "priority_rank" in settlements.columns:
            top = settlements.sort_values("priority_rank").head(6)
            status_color = {"critically_isolated": "#FF0000", "isolated": "#FF6600",
                            "partially_accessible": "#FFAA00", "accessible": "#44BB44"}
            # Header row clarifies the number is population
            rows += ("<tr><td style='padding:2px 0;color:#888;font-size:10.5px;'>Village · district</td>"
                     "<td style='text-align:right;color:#888;font-size:10.5px;'>Population</td></tr>")
            for _, r in top.iterrows():
                c = status_color.get(r.get("accessibility_status", ""), "#666")
                nm = r.get("name", "?")
                pop = int(r.get("est_population", 0) or 0)
                dist = r.get("district", "")
                rows += (f"<tr><td style='padding:3px 0;'>"
                         f"<span style='display:inline-block;width:8px;height:8px;border-radius:50%;"
                         f"background:{c};margin-right:6px;'></span>{nm}"
                         f"<span style='color:#888;'> · {dist}</span></td>"
                         f"<td style='text-align:right;color:#333;font-weight:600;'>{pop:,}</td></tr>")
    except Exception:
        rows = ""

    panel = f"""
    <div id="georeach-panel" style="
        position: fixed; top: 58px; right: 12px; width: 300px;
        background: rgba(255,255,255,0.97); border-radius: 10px;
        font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px;
        z-index: 9999; box-shadow: 0 4px 16px rgba(0,0,0,0.25);
        overflow: hidden; border: 1px solid #d8dee9;">
      <div onclick="var b=document.getElementById('gr-body'); b.style.display = b.style.display==='none'?'block':'none';"
           style="cursor:pointer; background:#1b3a5b; color:#fff; padding:9px 12px;
                  font-weight:600; display:flex; align-items:center; justify-content:space-between;">
        <span>Flood Impact Summary</span><span style="font-size:11px; opacity:.8;">▼ click to toggle</span>
      </div>
      <div id="gr-body" style="padding:12px;">
        <div style="display:flex; gap:8px; margin-bottom:10px;">
          <div style="flex:1; background:#fdecea; border-radius:8px; padding:8px; text-align:center;">
            <div style="font-size:22px; font-weight:700; color:#d7263d;">{cut}</div>
            <div style="font-size:11px; color:#a33;">settlements cut off<br>({pct_cut}% of {total})</div>
          </div>
          <div style="flex:1; background:#e8f0fe; border-radius:8px; padding:8px; text-align:center;">
            <div style="font-size:22px; font-weight:700; color:#1b3a5b;">{roads_pct}%</div>
            <div style="font-size:11px; color:#345;">of roads<br>flooded</div>
          </div>
        </div>
        <table style="width:100%; font-size:12px; border-collapse:collapse;">
          <tr style="border-bottom:1px solid #eee; color:#FF0000;"><td style="padding:3px 0;">● Critically isolated</td><td style="text-align:right;font-weight:bold;">{crit}</td></tr>
          <tr style="border-bottom:1px solid #eee; color:#FF6600;"><td style="padding:3px 0;">● Isolated</td><td style="text-align:right;font-weight:bold;">{iso}</td></tr>
          <tr style="border-bottom:1px solid #eee; color:#E39A00;"><td style="padding:3px 0;">● Partially accessible</td><td style="text-align:right;font-weight:bold;">{summary.get('partially_accessible',0)}</td></tr>
          <tr style="border-bottom:1px solid #eee; color:#2e9e2e;"><td style="padding:3px 0;">● Accessible</td><td style="text-align:right;font-weight:bold;">{summary.get('accessible',0)}</td></tr>
          <tr><td style="padding:3px 0;">Hospitals flooded</td><td style="text-align:right;font-weight:bold;">{summary.get('flooded_hospitals',0)}/{summary.get('total_hospitals',0)}</td></tr>
          <tr style="border-top:1px solid #eee;"><td style="padding:3px 0;">People without care (&lt;60 min)</td><td style="text-align:right;font-weight:bold;color:#d7263d;">{summary.get('population_without_care_60min',0):,}</td></tr>
          <tr><td style="padding:3px 0;">Lifeline bridges cut</td><td style="text-align:right;font-weight:bold;">{summary.get('flooded_bridges',0)}/{summary.get('total_bridges',0)}</td></tr>
          <tr><td style="padding:3px 0;">Relief staging points</td><td style="text-align:right;font-weight:bold;color:#6a0dad;">{summary.get('relief_staging_points',0)}</td></tr>
        </table>
        <div style="margin:10px 0 4px; font-weight:600; color:#1b3a5b;">🚑 Top priority to reach <span style="font-weight:400;color:#888;font-size:11px;">(number = population)</span></div>
        <table style="width:100%; font-size:12px; border-collapse:collapse;">{rows}</table>
        <div style="margin-top:10px; padding-top:8px; border-top:1px solid #eee; font-size:11px; color:#555; line-height:1.7;">
          <b>Map legend</b><br>
          <b>Roads:</b> <span style="color:#228B22;">━</span> open &nbsp; <span style="color:#FF0000;">━</span> flooded / blocked<br>
          <b>Flood:</b> <span style="color:#08519C;">▮</span> depth (darker = deeper)<br>
          <b>Villages (dots):</b><br>
          &nbsp;<span style="color:#FF0000;">●</span> critically isolated &nbsp; <span style="color:#FF6600;">●</span> isolated<br>
          &nbsp;<span style="color:#E39A00;">●</span> partially accessible &nbsp; <span style="color:#2e9e2e;">●</span> accessible<br>
          &nbsp;<span style="font-size:10px;color:#888;">dot size ∝ population</span><br>
          <b>Hospitals (pins):</b> <span style="color:#2e7d32;">✚</span> operational &nbsp; <span style="color:#c62828;">✚</span> flooded
        </div>
      </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(panel))
    return m


def add_download_button(m, settlements_gdf):
    """Add a 'Download relief action plan (CSV)' button.

    The CSV is embedded inline as a data-URI so the download works even on a
    static host (GitHub Pages) with no server.
    """
    if settlements_gdf is None or settlements_gdf.empty:
        return m
    try:
        from relief_planning import build_relief_table
        import tempfile, os as _os, base64
        tmp = _os.path.join(tempfile.gettempdir(), "_georeach_relief.csv")
        build_relief_table(settlements_gdf, tmp)
        with open(tmp, "r", encoding="utf-8") as f:
            csv_text = f.read()
        _os.remove(tmp)
    except Exception as e:
        logger.warning(f"  Could not embed relief CSV: {e}")
        return m

    b64 = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
    btn = f"""
    <a href="data:text/csv;base64,{b64}" download="relief_action_plan.csv"
       style="position: fixed; bottom: 92px; left: 10px; z-index: 9999;
              background:#6a0dad; color:#fff; text-decoration:none;
              font-family:'Segoe UI',Arial,sans-serif; font-size:12px; font-weight:600;
              padding:9px 14px; border-radius:8px; box-shadow:0 2px 8px rgba(0,0,0,0.3);">
      ⬇ Download relief action plan (CSV)
    </a>
    """
    m.get_root().html.add_child(folium.Element(btn))
    return m


def build_dashboard(results, output_path=None):
    """Build the complete interactive GIS dashboard.

    Args:
        results: dict from accessibility_assessment.run_accessibility_assessment()
        output_path: Path to save the HTML dashboard

    Returns:
        Folium Map object
    """
    logger.info("Building interactive GIS dashboard...")

    m = create_base_map()

    # Add layers in order (bottom to top)
    m = add_flood_layer(m, results.get("flood"))
    m = add_road_network_layer(m, results.get("roads"))
    m = add_emergency_corridors_layer(m, results.get("emergency_corridors", []))
    m = add_flooded_bridges_layer(m, results.get("bridges"))
    m = add_settlements_layer(m, results.get("settlements"))
    m = add_hospitals_layer(m, results.get("hospitals"))
    m = add_staging_points_layer(m, results.get("staging_points"))

    # Title banner + decision-support panel
    m = add_title_banner(m, scenario=results.get("summary", {}).get("scenario", "high"))
    m = add_summary_panel(m, results.get("summary", {}), results.get("settlements"))
    m = add_download_button(m, results.get("settlements"))

    # Add layer control (grouped, collapsed so it doesn't crowd the map)
    folium.LayerControl(collapsed=True, position="topleft").add_to(m)

    # Add map plugins
    plugins.Fullscreen(position="topleft").add_to(m)
    plugins.MeasureControl(position="bottomleft", primary_length_unit="kilometers").add_to(m)
    plugins.MiniMap(toggle_display=True, position="bottomright").add_to(m)
    # Geocoder search so authorities can jump to a place by name
    try:
        plugins.Geocoder(position="topleft", collapsed=True).add_to(m)
    except Exception:
        pass

    # Fit bounds to data
    if results.get("settlements") is not None and not results["settlements"].empty:
        bounds = results["settlements"].total_bounds  # [minx, miny, maxx, maxy]
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

    # Save
    if output_path is None:
        cfg = get_config()
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            cfg["paths"]["output"]
        )
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "georreach_dashboard.html")

    m.save(output_path)
    logger.info(f"Dashboard saved to: {output_path}")
    return m
