This folder is a bulk implementation of NHDPlus V2.1 HUC12 outlet (pour) point features in
Geoconnex. It templates the "from" pour points (fpp) GeoJSON from this URL
https://www.sciencebase.gov/catalog/file/get/60c7b895d34e86b9389b2a6c?name=fpp.geojson which is
the ScienceBase-hosted GeoJSON version of the "12-digit hydrologic unit outlet (pour) points for
the NHDPlus V2.1 WBD snapshot" USGS data release: https://doi.org/10.5066/P9SOT2VG

Each feature is a point marking where NHDPlus V2.1 flow-direction processing determined that
surface flow leaves a 12-digit hydrologic unit (HUC12). It is templated as a hyf:HY_HydroLocation
/ schema:Place linked via hyf:containingCatchment to its own `ref/huc12` reference catchment and,
when the downstream HUC12 is known, via hyf:lowerCatchment to the downstream `ref/huc12` catchment
or hyf:outflow for OCEAN/CANADA/MEXICO exits (mirroring the `ref:hu12` bulk runner's own tohuc
handling). Closed-basin sinks are flagged with hyf:HydroLocationType "sink" based on the source's
ISSINK attribute. The source's remaining diagnostic attributes (FDCODE, FILL, PCTPOUR, VPCTPOUR,
DOWNHUC, DOWNFLAG, TOHUC_DIST) are preserved as schema:additionalProperty PropertyValue entries.
