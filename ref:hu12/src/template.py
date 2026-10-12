# Copyright 2026 Lincoln Institute of Land Policy
# SPDX-License-Identifier: MIT

"""
Stream Geoconnex reference HUC12 watersheds as newline delimited JSON-LD to stdout.

Mirrors the pygeoapi JSON-LD item template used by reference.geoconnex.us:
https://github.com/internetofwater/reference.geoconnex.us/blob/main/pygeoapi-skin-dashboard/templates/jsonld/hu12/collections/items/item.jsonld
"""

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from multiprocessing import Pool
from pathlib import Path

import shapely
from shapely.geometry.base import BaseGeometry

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
LOGGER = logging.getLogger(__name__)

DEFAULT_GPKG = "https://www.hydroshare.org/resource/3295a17b4cc24d34bd6a5c5aaf753c50/data/contents/reference_huc12.gpkg"
TABLE = "reference_huc12"

CONTEXT = {
    "schema": "https://schema.org/",
    "gsp": "http://www.opengis.net/ont/geosparql#",
    "hyf": "https://www.opengis.net/def/schema/hy_features/hyf/",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
}

# Columns linking to the same HUC12 in other datasets, in template order
RELATED_COLUMNS = ("nhdpv2", "nhdphr", "niwaa", "final")

# GeoPackage binary header envelope sizes keyed by the envelope indicator flag
ENVELOPE_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def gpkg_geometry(blob: bytes) -> BaseGeometry:
    """Decode a GeoPackage geometry blob into a shapely geometry"""
    if blob[:2] != b"GP":
        raise ValueError("Not a GeoPackage geometry blob")
    envelope_indicator = (blob[3] >> 1) & 0b111
    return shapely.from_wkb(blob[8 + ENVELOPE_BYTES[envelope_indicator] :])


def is_huc(tohuc: str) -> bool:
    """Equivalent of the template's `data.tohuc | int(0)` truthiness check"""
    return tohuc.isdigit() and int(tohuc) != 0


def row_to_jsonld(row: dict) -> dict:
    huc12: str = row["huc12"]
    tohuc: str | None = row["tohuc"]
    name = (row["name"] or "").strip()

    # Geoconnex requires a name; a few thousand (mostly deprecated) HUC12s lack one upstream
    if not name:
        name = f"HUC12 {huc12}"

    feature: dict = {
        "@context": CONTEXT,
        "@id": row["uri"],
        "@type": ["hyf:HY_Catchment", "schema:Place"],
        "schema:name": name,
        "hyf:containingCatchment": [
            {"@id": f"https://geoconnex.us/ref/hu10/{huc12[0:10]}"},
            {"@id": f"https://geoconnex.us/ref/hu08/{huc12[0:8]}"},
            {"@id": f"https://geoconnex.us/ref/hu06/{huc12[0:6]}"},
            {"@id": f"https://geoconnex.us/ref/hu04/{huc12[0:4]}"},
            {"@id": f"https://geoconnex.us/ref/hu02/{huc12[0:2]}"},
        ],
    }

    if tohuc and is_huc(tohuc):
        feature["hyf:lowerCatchment"] = {
            "@id": f"https://geoconnex.us/ref/huc12/{tohuc}"
        }
    elif tohuc == "CLOSED BASIN":
        feature["@type"] = [
            "hyf:HY_Catchment",
            "hyf:HY_InteriorCatchment",
            "schema:Place",
        ]
    elif tohuc:
        # OCEAN, CANADA, MEXICO; the template would emit .../page/None for null tohuc
        feature["hyf:outflow"] = {
            "@id": f"https://dbpedia.org/page/{tohuc.capitalize()}"
        }

    # The template puts these under schema:subjectOf, but the Geoconnex SHACL
    # shape requires every subjectOf node to be a named dataset with a provider.
    # They are Geoconnex PIDs for this HUC12 in other datasets, so link them as
    # rdfs:seeAlso instead, matching the template's own variable name.
    see_also = [{"@id": row[column]} for column in RELATED_COLUMNS if row[column]]
    if see_also:
        feature["rdfs:seeAlso"] = see_also

    geometry = gpkg_geometry(row["geom"])
    feature["gsp:hasGeometry"] = {
        "@type": f"http://www.opengis.net/ont/sf#{geometry.geom_type}",
        "gsp:asWKT": {
            "@type": "http://www.opengis.net/ont/geosparql#wktLiteral",
            "@value": geometry.wkt,
        },
    }
    return feature


def process_row(row: dict) -> str:
    try:
        return json.dumps(row_to_jsonld(row), allow_nan=False)
    except ValueError as e:
        raise ValueError(f"Failed to serialize JSON-LD for {row['uri']}") from e


def download(url: str, destination: Path, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        try:
            LOGGER.info(f"Downloading {url} (attempt {attempt}/{attempts})")
            with urllib.request.urlopen(url) as response, open(destination, "wb") as f:
                shutil.copyfileobj(response, f, length=1024 * 1024)
            return
        except OSError as e:
            if attempt == attempts:
                raise
            LOGGER.warning(f"Download failed: {e}; retrying")
            time.sleep(5 * attempt)


@contextmanager
def local_gpkg(location: str) -> Iterator[Path]:
    """Yield a local path to the gpkg, downloading it to a temp dir if it is remote"""
    if Path(location).exists():
        yield Path(location)
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "reference_huc12.gpkg"
        download(location, path)
        yield path


def iter_rows(gpkg: Path) -> Iterator[dict]:
    """Yield each feature once, skipping rows without geometry and duplicate uris"""
    seen: set[str] = set()
    with sqlite3.connect(f"{gpkg.resolve().as_uri()}?mode=ro", uri=True) as conn:
        cursor = conn.execute(f'SELECT * FROM "{TABLE}" ORDER BY fid')
        # The live service reads from PostgreSQL, which lowercases the column names
        columns = [c[0].lower() for c in cursor.description]
        for values in cursor:
            row = dict(zip(columns, values))
            if row["geom"] is None:
                LOGGER.warning(f"Skipping {row['uri']}: no geometry")
                continue
            # A few uris appear twice in the source; keep the first
            if row["uri"] in seen:
                LOGGER.warning(f"Skipping duplicate {row['uri']} (fid {row['fid']})")
                continue
            seen.add(row["uri"])
            yield row


def main(location: str, workers: int) -> None:
    written = 0
    with local_gpkg(location) as gpkg, Pool(processes=workers) as pool:
        # Converting large polygons to WKT dominates the runtime, so spread it
        # across processes while imap keeps the output order deterministic
        for line in pool.imap(process_row, iter_rows(gpkg), chunksize=64):
            sys.stdout.write(line)
            sys.stdout.write("\n")
            written += 1
    sys.stdout.flush()
    LOGGER.info(f"Wrote {written} features")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gpkg",
        default=DEFAULT_GPKG,
        help="Path or URL of the reference_huc12 GeoPackage",
    )
    parser.add_argument(
        "--workers",
        type=int,
        # leave a core free for reading the gpkg and writing stdout
        default=max((os.cpu_count() or 1) - 1, 1),
        help="Number of worker processes used to build the JSON-LD",
    )
    args = parser.parse_args()
    main(args.gpkg, args.workers)
