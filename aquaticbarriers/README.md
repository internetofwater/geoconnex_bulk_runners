# aquaticbarriers bulk runner

Geoconnex bulk JSON-LD export of dams and assessed road/stream crossings from the
[National Aquatic Barrier Prioritization Tool](https://aquaticbarriers.org). See `AGENTS.md` for
design notes.

The container templates the newest `aquaticbarriers-cache-*` GitHub release. To refresh it, manually
run the **Export aquaticbarriers Geoparquet** workflow in GitHub Actions.

```sh
make deps    # install dependencies
make test    # run tests
make crawl   # re-crawl the ESRI FeatureServer into aquaticbarriers.parquet (~40 minutes)
make run     # template the local parquet cache to stdout as NDJSON
```
