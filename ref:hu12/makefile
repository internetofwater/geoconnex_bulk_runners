IMAGE ?= ref_hu12_bulk_rdf

deps:
	uv sync

test:
	.venv/bin/python -m pytest

# uv run refuses to run from a path containing ':' so call the venv python directly
run:
	.venv/bin/python src/template.py

build:
	docker build -t $(IMAGE) .

run_container:
	docker run --rm $(IMAGE)
