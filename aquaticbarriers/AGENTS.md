This folder is a bulk implementation of the National Aquatic Barrier Prioritization dams and
assessed road/stream crossings (https://aquaticbarriers.org, maintained by the Southeast Aquatic
Resources Partnership) in Geoconnex. The source is a single-layer ESRI FeatureServer
(https://services.arcgis.com/QVENGdaPbd4LUkLV/arcgis/rest/services/Aquatic_Barrier_Prioritization_Dams_Crossings_Indicators_05242024/FeatureServer/0)
with ~990k points (dams and surveyed road/stream crossings) that is paged 2000 features per request.

Because harvesting would require ~500 requests against a live ESRI server, `src/crawl.py` caches the
layer as GeoParquet (all ~250 source columns, WKB point geometry) and `src/template.py` templates
that cache to newline delimited JSON-LD. The cache is built by the manually triggered
`.github/workflows/aquaticbarriers_cache.yml` workflow, which publishes it as the
`aquaticbarriers.parquet` asset on a new `aquaticbarriers-cache-<run id>` release each run. With no
`--parquet`, `template.py` looks up the newest such release via the GitHub API (`latest_cache_url`),
so refreshing the cache is just re-running that workflow.

Each barrier is templated as a hyf:HY_HydroLocation / schema:Place keyed on its `SARPID`
(`https://geoconnex.us/nacc/barriers/{SARPID}`; the PID base is `PID_BASE` in
`template.py` and is provisional until a namespace is registered).

What is modeled beyond schema:additionalProperty:
- `hyf:HydroLocationType`: `dam`, or for crossings `culvert` / `bridge` (from `CrossingType`),
  otherwise `crossing`.
- `hyf:containingCatchment`: the `ref/huc12` catchment plus its hu10/hu08/hu06/hu04/hu02 parents,
  all derived from the HUC12 prefix like the `ref:hu12` runner.
- `schema:identifier`: `SARPID` and the National Inventory of Dams id.
- Upstream/downstream: HY_Features has no relation between two point locations, and the
  `hyf:referencedPosition` / `HY_IndirectPosition` pattern needs a flowline PID that Geoconnex
  doesn't have for the source's NHDPlus ids. So the nearest upstream/downstream barrier is an
  additionalProperty (`NearestUpstreamBarrier` / `NearestDownstreamBarrier`) whose value is the other
  barrier's PID. It is only emitted if that barrier is in the cache (`linkable_ids`), because the source
  also references waterfalls and barriers that were filtered out of this dataset.
- Network distances (`*Miles`) carry `schema:unitText` "miles".

Only a curated subset of columns is emitted; the species habitat and prioritization tier columns stay
in the cache only. The source encodes missing values as -1 / 0 / "Not applicable ..." which `clean()`
drops.
