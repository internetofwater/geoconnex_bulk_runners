# Copyright 2026 Lincoln Institute of Land Policy
# SPDX-License-Identifier: MIT

"""
Crawl the National Aquatic Barrier Prioritization ESRI FeatureServer into a GeoParquet file.

The layer holds ~990k dams and assessed road/stream crossings and the server only
returns 2000 features per request, so the crawl is cached once as GeoParquet and
`template.py` templates that cache instead of hitting the live server on every harvest.
"""

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import shapely

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
LOGGER = logging.getLogger(__name__)

LAYER_URL = (
    "https://services.arcgis.com/QVENGdaPbd4LUkLV/arcgis/rest/services/"
    "Aquatic_Barrier_Prioritization_Dams_Crossings_Indicators_05242024/FeatureServer/0"
)
PAGE_SIZE = 2000

ARROW_TYPES = {
    "esriFieldTypeOID": pa.int64(),
    "esriFieldTypeSmallInteger": pa.int64(),
    "esriFieldTypeInteger": pa.int64(),
    "esriFieldTypeBigInteger": pa.int64(),
    "esriFieldTypeSingle": pa.float64(),
    "esriFieldTypeDouble": pa.float64(),
    "esriFieldTypeString": pa.string(),
}

# Minimal GeoParquet 1.1 file metadata; the layer is served in EPSG:4326
GEO_METADATA = {
    "version": "1.1.0",
    "primary_column": "geometry",
    "columns": {
        "geometry": {
            "encoding": "WKB",
            "geometry_types": ["Point"],
            "crs": None,  # omitted CRS means OGC:CRS84 (lon/lat) per the spec
        }
    },
}

Fetch = Callable[[str, dict], dict]


def fetch_json(url: str, params: dict, attempts: int = 5) -> dict:
    query = urllib.parse.urlencode(params)
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(f"{url}?{query}", timeout=120) as response:
                body = json.load(response)
            # ESRI reports many failures as HTTP 200 with an error body
            if "error" in body:
                raise OSError(f"ESRI error: {body['error']}")
            return body
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as e:
            if attempt == attempts:
                raise
            LOGGER.warning(f"Request failed: {e}; retrying")
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def build_schema(layer: dict) -> pa.Schema:
    fields = []
    for field in layer["fields"]:
        if field["type"] not in ARROW_TYPES:
            raise ValueError(
                f"Unsupported ESRI field type {field['type']} ({field['name']})"
            )
        fields.append(pa.field(field["name"], ARROW_TYPES[field["type"]]))
    fields.append(pa.field("geometry", pa.binary()))
    return pa.schema(fields, metadata={"geo": json.dumps(GEO_METADATA)})


def iter_pages(
    fetch: Fetch, oid_field: str, page_size: int = PAGE_SIZE
) -> Iterator[list[dict]]:
    """Yield GeoJSON features page by page, keyset-paginated on the object id.

    Keyset pagination (`OID > last`) is used instead of resultOffset since deep
    offsets get slow on hosted feature layers and can skip or repeat rows if the
    layer is edited mid crawl.
    """
    last_oid = -1
    while True:
        page = fetch(
            f"{LAYER_URL}/query",
            {
                "where": f"{oid_field} > {last_oid}",
                "outFields": "*",
                "orderByFields": oid_field,
                "resultRecordCount": page_size,
                "f": "geojson",
            },
        )
        features = page["features"]
        if not features:
            return
        yield features
        last_oid = features[-1]["properties"][oid_field]


def features_to_table(features: list[dict], schema: pa.Schema) -> pa.Table:
    columns: dict[str, list] = {name: [] for name in schema.names}
    for feature in features:
        props = feature["properties"]
        for name in schema.names:
            if name != "geometry":
                columns[name].append(props.get(name))
        geometry = feature.get("geometry")
        columns["geometry"].append(
            shapely.to_wkb(shapely.Point(geometry["coordinates"][:2]))
            if geometry
            else None
        )
    return pa.table(columns, schema=schema)


def crawl(
    out: Path,
    limit: int | None = None,
    fetch: Fetch = fetch_json,
    page_size: int = PAGE_SIZE,
) -> int:
    layer = fetch(LAYER_URL, {"f": "json"})
    oid_field = layer["objectIdField"]
    schema = build_schema(layer)
    total = fetch(
        f"{LAYER_URL}/query", {"where": "1=1", "returnCountOnly": "true", "f": "json"}
    )["count"]
    LOGGER.info(f"Crawling {total} features into {out}")

    written = 0
    # Write to a temp name so an interrupted crawl never leaves a truncated cache
    partial = out.with_suffix(out.suffix + ".partial")
    with pq.ParquetWriter(partial, schema, compression="zstd") as writer:
        for features in iter_pages(fetch, oid_field, page_size):
            writer.write_table(features_to_table(features, schema))
            written += len(features)
            LOGGER.info(f"{written}/{total}")
            if limit is not None and written >= limit:
                break
    partial.replace(out)
    if limit is None and written != total:
        LOGGER.warning(f"Expected {total} features but crawled {written}")
    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("aquaticbarriers.parquet"))
    parser.add_argument(
        "--limit", type=int, help="Stop after roughly this many features"
    )
    args = parser.parse_args()
    crawl(args.out, args.limit)
