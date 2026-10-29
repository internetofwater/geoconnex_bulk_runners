# aquaticbarriers bulk runner

Geoconnex bulk JSON-LD export of dams and assessed road/stream crossings from the
[National Aquatic Barrier Prioritization Tool](https://aquaticbarriers.org). See `AGENTS.md` for
design notes.

```sh
make deps    # install dependencies
make test    # run tests
make crawl   # re-crawl the ESRI FeatureServer into aquaticbarriers.parquet (~40 minutes)
make run     # template the local parquet cache to stdout as NDJSON
```
