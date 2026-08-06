/**
 * GeoReach — Sentinel-1 SAR Flood Mapping (Google Earth Engine)
 * ============================================================
 * Produces an OBSERVED flood-extent GeoJSON for the 2026 Assam floods
 * over Dhemaji + Lakhimpur + Majuli, ready to drop into GeoReach.
 *
 * HOW TO RUN
 *   1. Go to https://code.earthengine.google.com  (free account; sign up once).
 *   2. Paste this whole script into the editor and click "Run".
 *   3. Inspect the blue flood layer on the map.
 *   4. In the "Tasks" tab, click "Run" on the export task
 *      (georeach_sar_flood_extent). Choose your Google Drive.
 *   5. Download the exported GeoJSON, rename it to:
 *          sar_flood_extent.geojson
 *      and place it in  D:\GeoReach\data\raw\
 *   6. Re-run the pipeline:  cd src && python main.py --scenario high
 *      GeoReach auto-detects it and uses the REAL flood instead of synthetic.
 *
 * METHOD
 *   Change detection on Sentinel-1 GRD (VV, dB sigma0, already calibrated in
 *   GEE). New open water appears as a strong backscatter DROP between a dry
 *   pre-flood baseline and the flood peak. Permanent water (the Brahmaputra
 *   channels) is removed using the JRC Global Surface Water layer, so only
 *   NEWLY inundated land is mapped.
 */

// ── 1. Area of interest: Dhemaji + Lakhimpur + Majuli ──
var aoi = ee.Geometry.Rectangle([93.6, 26.65, 95.3, 27.85]);
Map.centerObject(aoi, 9);

// ── 2. Date windows (2026 Assam floods peaked mid–late July) ──
var preStart  = '2026-04-01';   // dry pre-monsoon baseline
var preEnd    = '2026-05-15';
var floodStart = '2026-07-15';  // flood peak
var floodEnd   = '2026-07-31';

// ── 3. Load & filter Sentinel-1 GRD (VV, IW mode) ──
var s1 = ee.ImageCollection('COPERNICUS/S1_GRD')
  .filterBounds(aoi)
  .filter(ee.Filter.eq('instrumentMode', 'IW'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
  .select('VV');

// Use a single orbit direction for consistency (DESCENDING is common here;
// if the flood layer looks empty, switch to 'ASCENDING').
var orbit = 'DESCENDING';
s1 = s1.filter(ee.Filter.eq('orbitProperties_pass', orbit));

var pre   = s1.filterDate(preStart, preEnd).median().clip(aoi);
var flood = s1.filterDate(floodStart, floodEnd).median().clip(aoi);

// ── 4. Speckle reduction (smooth focal median) ──
var smooth = function(img){ return img.focal_median(50, 'circle', 'meters'); };
pre = smooth(pre);
flood = smooth(flood);

// ── 5. Change detection ──
var thrDb = -15;     // water is darker than about -15 dB in VV
var dropDb = -3;     // require a >=3 dB decrease vs baseline
var change = flood.subtract(pre);

var permanentWater = pre.lt(thrDb);            // already water before flood
var newFlood = flood.lt(thrDb)
                 .and(change.lt(dropDb))
                 .and(permanentWater.not());

// Remove JRC permanent water (rivers/lakes) to keep only new inundation
var jrc = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('occurrence');
var jrcPermanent = jrc.gt(50).unmask(0);       // >50% occurrence = permanent
newFlood = newFlood.where(jrcPermanent, 0).selfMask();

// ── 6. Visualise ──
Map.addLayer(pre,   {min:-25, max:0}, 'Pre-flood VV (dB)', false);
Map.addLayer(flood, {min:-25, max:0}, 'Flood VV (dB)', false);
Map.addLayer(newFlood, {palette:['#08519C']}, 'New Flood (SAR)');
Map.addLayer(aoi, {color:'red'}, 'AOI', false);

// ── 7. Vectorise and export as GeoJSON ──
var vectors = newFlood.reduceToVectors({
  geometry: aoi,
  scale: 30,                 // ~30 m
  geometryType: 'polygon',
  eightConnected: false,
  maxPixels: 1e10,
  bestEffort: true
});

// Attach a nominal depth so GeoReach's schema is satisfied. SAR gives extent,
// not depth; GeoReach treats presence of water as the flood signal. If you add
// a DEM later, depth can be refined from terrain.
vectors = vectors.map(function(f){
  return f.set({flood_depth_m: 1.5, scenario: 'observed_sar'});
});

// Drop tiny speckle polygons (< ~0.5 ha)
vectors = vectors.filter(ee.Filter.gte('count', 5));

print('Flood polygons (approx):', vectors.size());

Export.table.toDrive({
  collection: vectors,
  description: 'georeach_sar_flood_extent',
  fileNamePrefix: 'sar_flood_extent',
  fileFormat: 'GeoJSON'
});
