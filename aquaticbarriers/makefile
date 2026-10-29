IMAGE ?= aquaticbarriers_bulk_rdf
PARQUET ?= aquaticbarriers.parquet

deps:
	uv sync

test:
	.venv/bin/python -m pytest

# Re-crawl the live ESRI FeatureServer into a geoparquet cache (about 500 requests).
# Upload the result to the `aquaticbarriers-cache` GitHub release afterwards
crawl:
	.venv/bin/python src/crawl.py --out $(PARQUET)

# Template the locally cached parquet rather than downloading the release asset
run:
	.venv/bin/python src/template.py --parquet $(PARQUET)

build:
	docker build -t $(IMAGE) .

run_container:
	docker run --rm $(IMAGE)
