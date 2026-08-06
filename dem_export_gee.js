/**
 * GeoReach — DEM Export (Google Earth Engine)
 * ===========================================
 * Exports a 30 m elevation model (DEM) over Dhemaji + Lakhimpur + Majuli,
 * ready to drop into GeoReach. Used to derive REAL flood depth from terrain.
 *
 * HOW TO RUN  (same workflow as the flood script)
 *   1. https://code.earthengine.google.com  -> paste this -> Run.
 *   2. Tasks tab -> Run  georeach_dem_export  -> export to Google Drive.
 *   3. Download the GeoTIFF, rename it to:   srtm_dem.tif
 *   4. Put it in  D:\GeoReach\data\raw\
 *   5. Re-run:  cd src && python main.py --scenario high
 *
 * Uses NASA SRTM 30 m. To use the 30 m Copernicus DEM instead, swap the
 * `dem` line (commented below) — Copernicus is newer and often cleaner.
 */

var aoi = ee.Geometry.Rectangle([93.6, 26.65, 95.3, 27.85]);
Map.centerObject(aoi, 9);

// SRTM 30 m (void-filled)
var dem = ee.Image('USGS/SRTMGL1_003').select('elevation').clip(aoi);

// --- Alternative: Copernicus GLO-30 (30 m), usually better over floodplains ---
// var dem = ee.ImageCollection('COPERNICUS/DEM/GLO30')
//             .select('DEM').mosaic().clip(aoi).rename('elevation');

Map.addLayer(dem, {min: 50, max: 300,
  palette: ['#0d0887','#7e03a8','#cc4778','#f89540','#f0f921']}, 'Elevation (m)');

Export.image.toDrive({
  image: dem.toFloat(),
  description: 'georeach_dem_export',
  fileNamePrefix: 'srtm_dem',
  region: aoi,
  scale: 30,
  crs: 'EPSG:4326',
  maxPixels: 1e10
});
