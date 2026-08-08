GeoReach — QGIS Project (for submission)
========================================

This folder is a ready-to-open QGIS project with all layers pre-styled.

WHAT'S HERE
-----------
  GeoReach.qgs                     -> the QGIS project file (open this)
  *.geojson                        -> all input vector layers
  *.qml                            -> a style file for each layer (auto-applied)

ONE FILE YOU MUST ADD (too large to ship here)
----------------------------------------------
  srtm_dem.tif  -> copy it from  data/raw/srtm_dem.tif  into THIS folder.
  (The project references ./srtm_dem.tif for the terrain layer. It is turned
   OFF by default, so the project still opens fine without it — but add it so
   the raster/terrain layer is included as required.)

HOW TO USE
----------
  1. Copy data/raw/srtm_dem.tif into this folder (see above).
  2. Double-click GeoReach.qgs (QGIS 3.16 or newer — free from qgis.org).
  3. All layers load with styling:
        - Roads: red = flooded/blocked, green = open
        - Settlements: red = critically isolated, orange = isolated,
                       amber = partially accessible, green = accessible
        - Flood extent (blue), rivers (blue), bridges (brown),
          hospitals (green), relief staging points (purple), districts (outline)
  4. If a layer ever shows unstyled, right-click it -> Properties -> Symbology
     -> Style (bottom) -> Load Style -> pick the matching .qml.

TO SUBMIT
---------
  Upload this ENTIRE folder to Google Drive (including srtm_dem.tif),
  set sharing to "Anyone with the link -> Viewer", and share that link.

Note: paths are relative, so keep GeoReach.qgs in the same folder as the layers.
