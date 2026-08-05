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

    # Sample if too many cells (for performance)
    display_gdf = flood_gdf
    if len(flood_gdf) > 5000:
        display_gdf = flood_gdf.sample(5000, random_state=42)

    for idx, row in display_gdf.iterrows():
        depth_cat = row.get("depth_category", "moderate")
        color = depth_colors.get(str(depth_cat), "#2171B5")
        depth = row.get("flood_depth_m", 0)

        folium.GeoJson(
            row.geometry.__geo_interface__,
            style_function=lambda x, c=color, d=depth: {
                "fillColor": c,
                "color": c,
                "weight": 0,
                "fillOpacity": min(0.3 + d * 0.1, 0.7),
            },
            tooltip=f"Flood Depth: {depth:.1f}m",
        ).add_to(flood_group)

    flood_group.add_to(m)

    # Add depth legend
    depth_colormap = cm.LinearColormap(
        colors=["#AED8F0", "#5BA3D9", "#2171B5", "#08519C", "#08306B"],
        vmin=0, vmax=flood_gdf["flood_depth_m"].max(),
        caption="Flood Depth (meters)"
    )
    depth_colormap.add_to(m)

    return m


def add_road_network_layer(m, roads_gdf):
    """Add road network layer with flood status coloring."""
    if roads_gdf is None or roads_gdf.empty:
        return m

    logger.info("Adding road network layer...")
    cfg = get_config()
    colors = cfg["output"]["color_scheme"]

    roads_group = folium.FeatureGroup(name="Road Network", show=True)

    road_type_styles = {
        "motorway": {"weight": 4, "dashArray": None},
        "trunk": {"weight": 3.5, "dashArray": None},
        "primary": {"weight": 3, "dashArray": None},
        "secondary": {"weight": 2.5, "dashArray": None},
        "tertiary": {"weight": 2, "dashArray": None},
        "residential": {"weight": 1.5, "dashArray": "5 3"},
        "unclassified": {"weight": 1, "dashArray": "3 3"},
    }

    for idx, road in roads_gdf.iterrows():
        is_flooded = road.get("is_flooded", False)
        road_type = road.get("road_type", "unclassified")
        style = road_type_styles.get(road_type, {"weight": 1.5, "dashArray": None})

        color = colors["road_blocked"] if is_flooded else colors["road_open"]
        name = road.get("name", "Unnamed Road")
        if pd.isna(name):
            name = "Unnamed Road"

        tooltip_text = (
            f"<b>{name}</b><br>"
            f"Type: {road_type}<br>"
            f"Status: {'FLOODED' if is_flooded else 'Open'}"
        )
        if is_flooded:
            tooltip_text += f"<br>Flood Depth: {road.get('max_flood_depth', 0):.1f}m"

        folium.GeoJson(
            road.geometry.__geo_interface__,
            style_function=lambda x, c=color, w=style["weight"], d=style["dashArray"]: {
                "color": c,
                "weight": w,
                "dashArray": d or "",
                "opacity": 0.8,
            },
            tooltip=tooltip_text,
        ).add_to(roads_group)

    roads_group.add_to(m)
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


def add_summary_panel(m, summary):
    """Add a floating summary panel to the map."""
    summary_html = f"""
    <div style="
        position: fixed;
        top: 10px; right: 10px;
        width: 280px;
        background: rgba(255,255,255,0.95);
        border: 2px solid #333;
        border-radius: 8px;
        padding: 15px;
        font-family: Arial, sans-serif;
        font-size: 13px;
        z-index: 9999;
        box-shadow: 0 2px 10px rgba(0,0,0,0.3);
    ">
        <h3 style="margin:0 0 10px 0; color:#1a1a2e; border-bottom:2px solid #e94560; padding-bottom:5px;">
            GeoReach Dashboard
        </h3>
        <div style="margin-bottom:8px;">
            <b>Flood Impact Summary</b>
        </div>
        <table style="width:100%; font-size:12px; border-collapse:collapse;">
            <tr style="border-bottom:1px solid #eee;">
                <td style="padding:3px 0;">Total Settlements</td>
                <td style="text-align:right; font-weight:bold;">{summary.get('total_settlements', 0)}</td>
            </tr>
            <tr style="border-bottom:1px solid #eee; color:#FF0000;">
                <td style="padding:3px 0;">Critically Isolated</td>
                <td style="text-align:right; font-weight:bold;">{summary.get('critically_isolated', 0)}</td>
            </tr>
            <tr style="border-bottom:1px solid #eee; color:#FF6600;">
                <td style="padding:3px 0;">Isolated</td>
                <td style="text-align:right; font-weight:bold;">{summary.get('isolated', 0)}</td>
            </tr>
            <tr style="border-bottom:1px solid #eee; color:#FFAA00;">
                <td style="padding:3px 0;">Partially Accessible</td>
                <td style="text-align:right; font-weight:bold;">{summary.get('partially_accessible', 0)}</td>
            </tr>
            <tr style="border-bottom:1px solid #eee; color:#44BB44;">
                <td style="padding:3px 0;">Accessible</td>
                <td style="text-align:right; font-weight:bold;">{summary.get('accessible', 0)}</td>
            </tr>
            <tr style="border-bottom:1px solid #eee;">
                <td style="padding:3px 0;">Roads Flooded</td>
                <td style="text-align:right; font-weight:bold;">{summary.get('flooded_roads', 0)}/{summary.get('total_roads', 0)}</td>
            </tr>
            <tr>
                <td style="padding:3px 0;">Hospitals Flooded</td>
                <td style="text-align:right; font-weight:bold;">{summary.get('flooded_hospitals', 0)}/{summary.get('total_hospitals', 0)}</td>
            </tr>
        </table>
    </div>
    """
    m.get_root().html.add_child(folium.Element(summary_html))
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
    m = add_settlements_layer(m, results.get("settlements"))
    m = add_hospitals_layer(m, results.get("hospitals"))

    # Add summary panel
    m = add_summary_panel(m, results.get("summary", {}))

    # Add layer control
    folium.LayerControl(collapsed=False).add_to(m)

    # Add map plugins
    plugins.Fullscreen().add_to(m)
    plugins.MeasureControl(position="bottomleft").add_to(m)
    plugins.MiniMap(toggle_display=True).add_to(m)

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
