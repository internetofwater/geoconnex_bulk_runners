# Copyright 2026 Lincoln Institute of Land Policy
# SPDX-License-Identifier: MIT

"""
Stream NHDPlus V2.1 HUC12 outlet (pour) points as newline delimited JSON-LD to stdout.

Source: "12-digit hydrologic unit outlet (pour) points for the NHDPlus V2.1 WBD
snapshot", "from" pour points (fpp) dataset, USGS data release
https://doi.org/10.5066/P9SOT2VG. Each point marks where NHDPlus V2.1 flow-direction
processing determined that surface flow leaves a HUC12, and is linked to its own and
(when known) its downstream `ref/huc12` reference catchment, mirroring the tohuc
handling in the `ref:hu12` bulk runner.
"""

import argparse
import json
import logging
import re
import shutil
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
LOGGER = logging.getLogger(__name__)

DEFAULT_GEOJSON = (
    "https://www.sciencebase.gov/catalog/file/get/60c7b895d34e86b9389b2a6c"
    "?name=fpp.geojson"
)

SOURCE_DATASET_NAME = (
    '12-digit hydrologic unit outlet (pour) points for the NHDPlus V2.1 WBD '
    'snapshot: "from" pour points (fpp)'
)
SOURCE_DOI = "doi:10.5066/P9SOT2VG"
SOURCE_URL = "https://doi.org/10.5066/P9SOT2VG"

CONTEXT = {
    "schema": "https://schema.org/",
    "gsp": "http://www.opengis.net/ont/geosparql#",
    "hyf": "https://www.opengis.net/def/schema/hy_features/hyf/",
}

HUC12_RE = re.compile(r"^\d{12}$")

# HU_12_DS values meaning flow leaves the WBD snapshot entirely, matching the
# dbpedia outflow convention used by the ref:hu12 bulk runner's tohuc handling
OUTFLOW_PAGES = {
    "OCEAN": "https://dbpedia.org/page/Ocean",
    "CANADA": "https://dbpedia.org/page/Canada",
    "MEXICO": "https://dbpedia.org/page/Mexico",
}

# (source column, human readable definition) for attributes kept only for
# fidelity with the source data, per the dataset's FGDC entity/attribute metadata
ADDITIONAL_PROPERTIES = (
    (
        "FDCODE",
        (
            "ESRI flow direction code at the pour point "
            "(0=Sink, 1=East, 2=Southeast, 4=South, 8=Southwest, "
            "16=West, 32=Northwest, 64=North, 128=Northeast)"
        ),
    ),
    (
        "FILL",
        (
            'Whether the elevation raster was filled to determine a "fill and spill" '
            "flow direction at this pour point (0=not filled, 1=filled)"
        ),
    ),
    (
        "PCTPOUR",
        (
            "Percentage of the HU12 flow accumulation raster cells represented at "
            "this pour point location"
        ),
    ),
    (
        "VPCTPOUR",
        (
            "Percentage of the HU12 flow accumulation raster cells represented at "
            "the related vector pour point location"
        ),
    ),
    (
        "DOWNHUC",
        (
            "HU12 downstream of this pour point per spatial overlay with the WBD "
            "snapshot; may disagree with HU_12_DS"
        ),
    ),
    (
        "DOWNFLAG",
        (
            "Comparison of the flow-direction-derived downstream HU12 to HU_12_DS "
            "(0=match, 1=mismatch, 2=HU_12_DS is null)"
        ),
    ),
    (
        "TOHUC_DIST",
        "Distance in meters between this pour point and the downstream HU12 polygon",
    ),
)


def additional_properties(props: dict) -> list[dict]:
    values = []
    for key, description in ADDITIONAL_PROPERTIES:
        value = props.get(key)
        if value is None:
            continue
        values.append(
            {
                "@type": "schema:PropertyValue",
                "schema:name": key,
                "schema:value": value,
                "schema:description": description,
            }
        )
    return values


def row_to_jsonld(props: dict, coordinates: list[float]) -> dict:
    huc12: str = props["HUC_12"]
    is_sink = bool(props["ISSINK"])
    downstream = (props.get("HU_12_DS") or "").strip()
    normalized_downstream = downstream.upper()

    feature: dict = {
        "@context": CONTEXT,
        "@id": f"https://geoconnex.us/nhdplusv2/huc12/{huc12}",
        "@type": ["hyf:HY_HydroLocation", "schema:Place"],
        "schema:name": f"NHDPlus V2.1 outlet pour point for HUC12 {huc12}",
        "hyf:HydroLocationType": "sink" if is_sink else "outlet",
        "hyf:containingCatchment": {
            "@id": f"https://geoconnex.us/ref/huc12/{huc12}"
        },
    }

    if is_sink:
        feature["schema:description"] = (
            f"Derived outlet where NHDPlus V2.1 flow direction processing "
            f"indicates HUC12 {huc12} is a closed-basin sink with no surface "
            "outflow to another hydrologic unit."
        )
    else:
        feature["schema:description"] = (
            "Location where NHDPlus V2.1 flow direction processing indicates "
            f"surface flow leaves HUC12 {huc12}."
        )

    if HUC12_RE.match(downstream):
        feature["hyf:lowerCatchment"] = {
            "@id": f"https://geoconnex.us/ref/huc12/{downstream}"
        }
    elif normalized_downstream in OUTFLOW_PAGES:
        feature["hyf:outflow"] = {"@id": OUTFLOW_PAGES[normalized_downstream]}
    elif downstream and normalized_downstream != "CLOSED BASIN":
        # A small number of records carry malformed or placeholder downstream
        # codes (e.g. "17040209XXXX") where the WBD snapshot was not properly
        # encoded; leave the downstream relationship out rather than guess
        LOGGER.warning(f"Unrecognized HU_12_DS {downstream!r} for HUC12 {huc12}")

    feature["schema:subjectOf"] = {
        "@type": "schema:Dataset",
        "schema:name": SOURCE_DATASET_NAME,
        "schema:identifier": SOURCE_DOI,
        "schema:url": SOURCE_URL,
        "schema:provider": {
            "@type": "schema:GovernmentOrganization",
            "schema:name": "U.S. Geological Survey",
            "schema:url": "https://www.usgs.gov/",
        },
    }

    properties = additional_properties(props)
    if properties:
        feature["schema:additionalProperty"] = properties

    lon, lat = coordinates
    feature["gsp:hasGeometry"] = {
        "@type": "http://www.opengis.net/ont/sf#Point",
        "gsp:asWKT": {
            "@type": "http://www.opengis.net/ont/geosparql#wktLiteral",
            "@value": f"POINT ({lon} {lat})",
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
def local_geojson(location: str) -> Iterator[Path]:
    """Yield a local path to the fpp GeoJSON, downloading it to a temp dir if remote"""
    if Path(location).exists():
        yield Path(location)
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fpp.geojson"
        download(location, path)
        yield path


def iter_features(path: Path) -> Iterator[dict]:
    with open(path) as f:
        collection = json.load(f)
    yield from collection["features"]


def main(location: str) -> None:
    seen: set[str] = set()
    written = 0
    with local_geojson(location) as path:
        for feature in iter_features(path):
            props = feature["properties"]
            huc12 = props["HUC_12"]
            geometry = feature.get("geometry")
            if not geometry or geometry.get("type") != "Point":
                LOGGER.warning(f"Skipping HUC12 {huc12}: missing or non-point geometry")
                continue
            # A handful of HUC12s appear twice in the source; keep the first
            if huc12 in seen:
                LOGGER.warning(f"Skipping duplicate HUC12 {huc12}")
                continue
            seen.add(huc12)
            doc = row_to_jsonld(props, geometry["coordinates"])
            sys.stdout.write(json.dumps(doc, allow_nan=False))
            sys.stdout.write("\n")
            written += 1
    sys.stdout.flush()
    LOGGER.info(f"Wrote {written} features")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--geojson",
        default=DEFAULT_GEOJSON,
        help="Path or URL of the fpp (from pour points) GeoJSON",
    )
    args = parser.parse_args()
    main(args.geojson)
