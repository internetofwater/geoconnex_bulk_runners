# Copyright 2026 Lincoln Institute of Land Policy
# SPDX-License-Identifier: MIT

"""
Stream Geoconnex reference gages as newline delimited JSON-LD to stdout.

Mirrors the pygeoapi JSON-LD item template used by reference.geoconnex.us:
https://github.com/internetofwater/reference.geoconnex.us/blob/main/pygeoapi-skin-dashboard/templates/jsonld/gages/collections/items/item.jsonld
"""

import argparse
import json
import logging
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import shapely
from shapely.geometry.base import BaseGeometry

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
LOGGER = logging.getLogger(__name__)

DEFAULT_GPKG = "https://www.hydroshare.org/resource/3295a17b4cc24d34bd6a5c5aaf753c50/data/contents/ref_gages.gpkg"
TABLE = "ref_gages"

CONTEXT = {
    "schema": "https://schema.org/",
    "gsp": "http://www.opengis.net/ont/geosparql#",
    "hyf": "https://www.opengis.net/def/schema/hy_features/hyf/",
}

# The upstream template labels every dataset provider "USGS"; ref_gages
# also contains gages from other agencies, so name them by their url
PROVIDER_NAMES = {
    "https://waterdata.usgs.gov": "USGS",
    "https://cdec.water.ca.gov": "California Department of Water Resources",
    "https://dwr.state.co.us": "Colorado Division of Water Resources",
}

# GeoPackage binary header envelope sizes keyed by the envelope indicator flag
ENVELOPE_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def gpkg_geometry(blob: bytes) -> BaseGeometry:
    """Decode a GeoPackage geometry blob into a shapely geometry"""
    if blob[:2] != b"GP":
        raise ValueError("Not a GeoPackage geometry blob")
    envelope_indicator = (blob[3] >> 1) & 0b111
    return shapely.from_wkb(blob[8 + ENVELOPE_BYTES[envelope_indicator] :])


def clean_text(value: str | None) -> str | None:
    """Equivalent of the template's `replace("\\\\\\%", "")` filter"""
    if value is None:
        return None
    return value.replace("\\\\\\%", "")


def provider_name(url: str | None) -> str:
    if not url:
        return "Unknown"
    return PROVIDER_NAMES.get(url.rstrip("/"), urlparse(url).netloc or url)


def row_to_jsonld(row: dict) -> dict:
    name = clean_text(row["name"])
    description = clean_text(row["description"])
    provider = row["provider"]
    subjectof = row["subjectof"]
    nws_url = row["nws_url"]
    reachcode = row["nhdpv2_reachcode"]
    reach_measure = row["nhdpv2_reach_measure"]
    mainstem_uri = row["mainstem_uri"]

    # Geoconnex requires a name; a few thousand gages lack one upstream
    if not name:
        name = row["provider_id"] or str(row["id"])

    feature: dict = {
        "@context": CONTEXT,
        "@id": row["uri"],
        "@type": [
            "hyf:HY_HydrometricFeature",
            "hyf:HY_HydroLocation",
            "schema:Place",
        ],
        "hyf:HY_HydroLocationType": "hydrometricStation",
        "schema:name": name,
    }
    if description:
        feature["schema:description"] = description
    if provider:
        feature["schema:provider"] = provider

    subject_of = []
    if subjectof:
        subject_of.append(
            {
                "@type": "schema:Dataset",
                "schema:name": description or name,
                "schema:url": subjectof,
                "schema:provider": {
                    "@type": "schema:GovernmentOrganization",
                    "schema:name": provider_name(provider),
                    "schema:url": provider,
                },
            }
        )
    if nws_url:
        subject_of.append(
            {
                "@type": "schema:WebPage",
                # every subjectOf node must have a name to satisfy the Geoconnex SHACL shape
                "schema:name": f"NOAA National Water Prediction Service gauge page for {name}",
                "schema:url": nws_url,
                "schema:provider": {
                    "@type": "schema:GovernmentOrganization",
                    "schema:name": "NOAA",
                    "schema:url": "https://water.noaa.gov/",
                },
            }
        )
    if subject_of:
        feature["schema:subjectOf"] = subject_of

    referenced_position = []
    if reachcode:
        indirect_position: dict = {}
        if reach_measure is not None:
            indirect_position["hyf:distanceExpression"] = {
                "hyf:HY_DistanceFromReferent": {"hyf:interpolative": reach_measure}
            }
            indirect_position["hyf:distanceDescription"] = {
                "hyf:HY_DistanceDescription": "upstream"
            }
        indirect_position["hyf:linearElement"] = {
            "@id": f"https://geoconnex.us/nhdplusv2/reachcode/{reachcode}"
        }
        referenced_position.append({"hyf:HY_IndirectPosition": indirect_position})
    if mainstem_uri:
        referenced_position.append(
            {"hyf:HY_IndirectPosition": {"hyf:linearElement": {"@id": mainstem_uri}}}
        )
    if referenced_position:
        feature["hyf:referencedPosition"] = referenced_position

    geometry = gpkg_geometry(row["geom"])
    feature["gsp:hasGeometry"] = {
        "@type": f"http://www.opengis.net/ont/sf#{geometry.geom_type}",
        "gsp:asWKT": {
            "@type": "http://www.opengis.net/ont/geosparql#wktLiteral",
            "@value": geometry.wkt,
        },
    }
    return feature


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
        path = Path(tmp) / "ref_gages.gpkg"
        download(location, path)
        yield path


def iter_rows(gpkg: Path) -> Iterator[dict]:
    with sqlite3.connect(f"{gpkg.resolve().as_uri()}?mode=ro", uri=True) as conn:
        cursor = conn.execute(f'SELECT * FROM "{TABLE}" ORDER BY fid')
        # The live service reads from PostgreSQL, which lowercases the column names
        columns = [c[0].lower() for c in cursor.description]
        for values in cursor:
            yield dict(zip(columns, values))


def main(location: str) -> None:
    seen: set[str] = set()
    written = 0
    with local_gpkg(location) as gpkg:
        for row in iter_rows(gpkg):
            if row["geom"] is None:
                LOGGER.warning(f"Skipping {row['uri']}: no geometry")
                continue
            # A handful of uris appear twice in the source with different gages;
            # keep the first so each @id describes a single feature
            if row["uri"] in seen:
                LOGGER.warning(f"Skipping duplicate {row['uri']} (fid {row['fid']})")
                continue
            seen.add(row["uri"])
            sys.stdout.write(json.dumps(row_to_jsonld(row), allow_nan=False))
            sys.stdout.write("\n")
            written += 1
    sys.stdout.flush()
    LOGGER.info(f"Wrote {written} features")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gpkg",
        default=DEFAULT_GPKG,
        help="Path or URL of the ref_gages GeoPackage",
    )
    args = parser.parse_args()
    main(args.gpkg)
