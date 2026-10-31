#!/bin/sh
# Throwaway (geo plan 1, Task 3): build one small sample per candidate format
# so they can be tried in iTAK before converting anything at scale.
# Run inside ghcr.io/osgeo/gdal:ubuntu-full-3.13.3 with OUT mounted:
#   docker run --rm -v "$OUT:/out" -v "$PWD/apps/geo/tools:/tools:ro" \
#     ghcr.io/osgeo/gdal:ubuntu-full-3.13.3 sh /tools/make_itak_samples.sh
set -eu
UA="geo-sync/1.0 (offline library; https://github.com/lfprocks/homelab)"
cd /out
rm -rf samples && mkdir -p src samples/DTED/w123
get() {
  [ -s "src/$2" ] || python3 -c 'import sys,urllib.request,shutil
r=urllib.request.urlopen(urllib.request.Request(sys.argv[1],headers={"User-Agent":sys.argv[3]}),timeout=300)
shutil.copyfileobj(r,open(sys.argv[2],"wb"),8<<20)' "$1" "src/$2" "$UA"
}

# (a)(b) ALPR points from the Washington OSM extract.
get https://download.geofabrik.de/north-america/us/washington-latest.osm.pbf wa.osm.pbf
ogr2ogr -f GPKG src/alpr_all.gpkg src/wa.osm.pbf points \
  -where "other_tags LIKE '%\"surveillance:type\"=>\"ALPR\"%'" -nln alpr
# LIBKML takes its label/description fields from the environment.
LIBKML_NAME_FIELD=name LIBKML_DESCRIPTION_FIELD=other_tags \
  ogr2ogr -f LIBKML samples/alpr_sample.kmz src/alpr_all.gpkg alpr -limit 50
ogr2ogr -f GPKG samples/alpr_sample.gpkg src/alpr_all.gpkg alpr -limit 50

# (c) USFS ownership, clipped to roughly Bonner County.
get https://data.fs.usda.gov/geodata/edw/edw_resources/fc/BdyOwn_LSRS_BasicOwnership.gdb.zip usfs_own.gdb.zip
LYR=$(ogrinfo -q -ro "/vsizip/src/usfs_own.gdb.zip" | awk -F': ' 'NR==1{print $2}' | awk '{print $1}')
ogr2ogr -f LIBKML samples/usfs_ownership_bonner.kmz "/vsizip/src/usfs_own.gdb.zip" "$LYR" \
  -t_srs EPSG:4326 -spat -117.30 47.98 -116.03 48.75 -spat_srs EPSG:4326 -nlt PROMOTE_TO_MULTI -makevalid
ogr2ogr -f GPKG samples/usfs_ownership_bonner.gpkg "/vsizip/src/usfs_own.gdb.zip" "$LYR" \
  -t_srs EPSG:4326 -spat -117.30 47.98 -116.03 48.75 -spat_srs EPSG:4326 -nlt PROMOTE_TO_MULTI -makevalid

# (d) Seattle VFR sectional as MBTiles, z6-11.
get https://aeronav.faa.gov/visual/09-03-2026/sectional-files/Seattle.zip seattle.zip
TIF=$(unzip -Z1 src/seattle.zip | grep -i '\.tif$' | head -1)
gdal_translate -of VRT -expand rgb "/vsizip/src/seattle.zip/$TIF" src/seattle_rgb.vrt
gdal_translate -of MBTILES -co TILE_FORMAT=JPEG -co ZOOM_LEVEL_STRATEGY=LOWER src/seattle_rgb.vrt samples/faa_seattle_sample.mbtiles
gdaladdo -r average samples/faa_seattle_sample.mbtiles 2 4 8 16 32

# (e) DTED level 2 for 47-48N 122-123W from 3DEP 1/3 arc-second.
get https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/n48w123/USGS_13_n48w123.tif dem_n48w123.tif
gdalwarp -overwrite -t_srs EPSG:4326 -te -123 47 -122 48 -ts 3601 3601 -r bilinear -ot Int16 src/dem_n48w123.tif src/n47_warp.tif
gdal_translate -of DTED src/n47_warp.tif samples/DTED/w123/n47.dt2

# (f) King County 2025 aerials as a live online map source.
cat > samples/kingco_aerial_2025.xml <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<customMapSource>
  <name>KingCo Aerial 2025</name>
  <minZoom>0</minZoom>
  <maxZoom>21</maxZoom>
  <tileType>jpg</tileType>
  <tileUpdate>None</tileUpdate>
  <url>https://gismaps.kingcounty.gov/arcgis/rest/services/BaseMaps/KingCo_Aerial_2025/MapServer/tile/{$z}/{$y}/{$x}</url>
  <backgroundColor>#000000</backgroundColor>
</customMapSource>
XML

# (g) A TAK data package bundling (a)-(e).
rm -rf pkg && mkdir -p pkg/MANIFEST pkg/DTED/w123
cp samples/alpr_sample.kmz samples/usfs_ownership_bonner.kmz samples/faa_seattle_sample.mbtiles pkg/
cp samples/DTED/w123/n47.dt2 pkg/DTED/w123/
UID=$(cat /proc/sys/kernel/random/uuid)
{
  echo '<MissionPackageManifest version="2"><Configuration>'
  echo "<Parameter name=\"uid\" value=\"$UID\"/><Parameter name=\"name\" value=\"geo-samples\"/>"
  echo '</Configuration><Contents>'
  for f in alpr_sample.kmz usfs_ownership_bonner.kmz faa_seattle_sample.mbtiles DTED/w123/n47.dt2; do
    echo "<Content ignore=\"false\" zipEntry=\"$f\"/>"
  done
  echo '</Contents></MissionPackageManifest>'
} > pkg/MANIFEST/manifest.xml
(cd pkg && rm -f ../samples/samples.zip && python3 -c "import shutil; shutil.make_archive('../samples/samples', 'zip', '.')")
ls -la samples samples/DTED/w123
