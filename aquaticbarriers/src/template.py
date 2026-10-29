# Copyright 2026 Lincoln Institute of Land Policy
# SPDX-License-Identifier: MIT

"""
Stream National Aquatic Barrier Prioritization dams and road/stream crossings as
newline delimited JSON-LD to stdout.

Source: the Southeast Aquatic Resources Partnership's aquaticbarriers.org output
dataset, served from an ESRI FeatureServer. Because that server pages 2000 features
per request, `crawl.py` caches it as GeoParquet (published as a GitHub release asset)
and this script templates the cached file.
"""

import argparse
import json
import logging
import math
import shutil
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pyarrow.parquet as pq
import shapely

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
LOGGER = logging.getLogger(__name__)

DEFAULT_PARQUET = (
    "https://github.com/internetofwater/geoconnex_bulk_runners/releases/download/"
    "aquaticbarriers-cache/aquaticbarriers.parquet"
)

PID_BASE = "https://geoconnex.us/nacc/barriers"

SOURCE_DATASET_NAME = (
    "National Aquatic Barrier Prioritization: dams and assessed road/stream crossings"
)
SOURCE_URL = "https://aquaticbarriers.org"
PROVIDER_NAME = "Southeast Aquatic Resources Partnership"
PROVIDER_URL = "https://southeastaquatics.net/"

CONTEXT = {
    "schema": "https://schema.org/",
    "gsp": "http://www.opengis.net/ont/geosparql#",
    "hyf": "https://www.opengis.net/def/schema/hy_features/hyf/",
}

# BarrierType value in the source -> (hyf:HydroLocationType, fallback name prefix)
BARRIER_TYPES = {
    "Dam": ("dam", "Dam"),
    "Surveyed road/stream crossing": ("crossing", "Surveyed road/stream crossing"),
}

# CrossingType values specific enough to be a more useful hyf:HydroLocationType
# than the generic "crossing"; the rest (ford, tide gate, unknown, ...) stay generic
CROSSING_LOCATION_TYPES = {"Culvert": "culvert", "Bridge": "bridge"}

# Reference catchments containing the barrier, derived from the prefixes of its HUC12
# the same way the ref:hu12 bulk runner chains HUC12s up to their parents
HUC_NAMESPACES = (
    ("huc12", 12),
    ("hu10", 10),
    ("hu08", 8),
    ("hu06", 6),
    ("hu04", 4),
    ("hu02", 2),
)

# (source column, name of the schema:identifier scheme)
IDENTIFIERS = (
    ("SARPID", "SARPID"),
    ("NIDID", "National Inventory of Dams ID"),
)

# (source column, property name, definition) for the nearest barrier along the flow
# network. HY_Features has no relation between two point locations, so these are
# emitted as additionalProperty links to the other barrier's PID, and only when that
# barrier is in this dataset (the source also points at waterfalls and barriers that
# were filtered out)
NETWORK_LINKS = (
    (
        "UpstreamBarrierSARPID",
        "NearestUpstreamBarrier",
        "Nearest barrier upstream of this barrier along the flow network",
    ),
    (
        "DownstreamBarrierSARPID",
        "NearestDownstreamBarrier",
        "Nearest barrier downstream of this barrier along the flow network",
    ),
)

# (source column, definition, unit) for attributes carried over as
# schema:additionalProperty. The cache keeps all ~250 source columns, but most of the
# network analysis indicators (species habitat, prioritization tiers) are analysis
# outputs that would swamp the description of the feature itself
ADDITIONAL_PROPERTIES = (
    ("Source", "Dataset the barrier record was compiled from", None),
    ("SourceID", "Identifier of the barrier within its source dataset", None),
    ("River", "Name of the stream or river the barrier is on", None),
    ("NHDPlusID", "NHDPlus flowline identifier the barrier was snapped to", None),
    ("YearCompleted", "Year the dam was completed", None),
    ("Height", "Height of the dam", None),
    ("Length", "Length of the dam", None),
    ("Width", "Width of the dam", None),
    ("StorageVolume", "Storage volume of the dam's impoundment", None),
    ("Hazard", "Hazard potential classification of the dam", None),
    ("Construction", "Construction type of the dam", None),
    ("Purpose", "Primary purpose of the dam", None),
    ("Condition", "Assessed condition of the barrier", None),
    ("LowheadDam", "Whether the dam is a lowhead dam", None),
    ("Passability", "Assessed fish passability of the barrier", None),
    ("Removed", "Whether the barrier has been removed", None),
    ("YearRemoved", "Year the barrier was removed", None),
    ("Road", "Name of the road crossing the stream", None),
    ("RoadType", "Surface or class of the road at the crossing", None),
    ("CrossingType", "Structure type of the road/stream crossing", None),
    (
        "Constriction",
        "Degree to which the crossing constricts the stream channel",
        None,
    ),
    (
        "BarrierSeverity",
        "Assessed barrier severity of the road/stream crossing",
        None,
    ),
    ("YearSurveyed", "Year the crossing was surveyed", None),
    ("State", "State the barrier is located in", None),
    ("County", "County the barrier is located in", None),
    ("SourceLink", "Link to the barrier's page in its original source", None),
    (
        "UpstreamBarrierMiles",
        "Distance along the network to the nearest upstream barrier",
        "miles",
    ),
    (
        "DownstreamBarrierMiles",
        "Distance along the network to the nearest downstream barrier",
        "miles",
    ),
    (
        "TotalUpstreamMiles",
        "Total miles of the river network upstream of the barrier",
        "miles",
    ),
    (
        "FreeDownstreamMiles",
        (
            "Miles of river network downstream of the barrier that flow freely, "
            "i.e. before the next barrier"
        ),
        "miles",
    ),
    (
        "MilesToOutlet",
        "Distance along the network from the barrier to its outlet",
        "miles",
    ),
)


def clean(value):
    """Return None for values the source uses to mean missing or not applicable.

    The source encodes missing numbers as -1 (and 0 for years and physical
    measurements) and non-applicable text as "Not applicable ...", so those are
    dropped rather than emitted as misleading facts.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value or value.lower().startswith("not applicable"):
            return None
        return value
    if isinstance(value, float) and math.isnan(value):
        return None
    if value <= 0:
        return None
    return value


def property_value(name: str, value, description: str, unit: str | None = None):
    node = {
        "@type": "schema:PropertyValue",
        "schema:name": name,
        "schema:value": value,
        "schema:description": description,
    }
    if unit:
        node["schema:unitText"] = unit
    return node


def additional_properties(
    row: dict, own_sarpid: str, linkable: frozenset[str] | set[str]
) -> list[dict]:
    values = []
    for key, description, unit in ADDITIONAL_PROPERTIES:
        value = clean(row.get(key))
        if value is None:
            continue
        # Parquet floats come from single precision ESRI fields; keep ints as ints
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        values.append(property_value(key, value, description, unit))

    for column, name, description in NETWORK_LINKS:
        target = clean(row.get(column))
        if target and target != own_sarpid and target in linkable:
            values.append(
                property_value(name, {"@id": f"{PID_BASE}/{target}"}, description)
            )
    return values


def identifiers(row: dict) -> list[dict]:
    return [
        {
            "@type": "schema:PropertyValue",
            "schema:propertyID": scheme,
            "schema:value": value,
        }
        for column, scheme in IDENTIFIERS
        if (value := clean(row.get(column)))
    ]


def containing_catchments(huc12: str | None) -> list[dict]:
    if not huc12 or len(huc12) != 12 or not huc12.isdigit():
        return []
    return [
        {"@id": f"https://geoconnex.us/ref/{namespace}/{huc12[:digits]}"}
        for namespace, digits in HUC_NAMESPACES
    ]


def row_to_jsonld(row: dict, linkable: frozenset[str] | set[str] = frozenset()) -> dict:
    """Template one cache row. `linkable` is the set of SARPIDs present in the dataset,
    which network links to other barriers are restricted to so none dangle"""
    sarpid: str = row["SARPID"]
    barrier_type = row["BarrierType"]
    if barrier_type not in BARRIER_TYPES:
        raise ValueError(f"Unrecognized BarrierType {barrier_type!r} for {sarpid}")
    location_type, fallback_prefix = BARRIER_TYPES[barrier_type]
    if location_type == "crossing":
        location_type = CROSSING_LOCATION_TYPES.get(
            clean(row.get("CrossingType")), location_type
        )

    # Geoconnex requires a name; ~20% of records lack one upstream
    name = clean(row.get("Name")) or f"{fallback_prefix} {sarpid}"

    feature: dict = {
        "@context": CONTEXT,
        "@id": f"{PID_BASE}/{sarpid}",
        "@type": ["hyf:HY_HydroLocation", "schema:Place"],
        "schema:name": name,
        "hyf:HydroLocationType": location_type,
    }

    description = f"{fallback_prefix} {sarpid}"
    place = ", ".join(
        p for p in (clean(row.get("County")), clean(row.get("State"))) if p
    )
    if place:
        description += f" in {place}"
    river = clean(row.get("River"))
    if river:
        description += f" on {river}"
    feature["schema:description"] = description + "."

    catchments = containing_catchments(clean(row.get("HUC12")))
    if catchments:
        feature["hyf:containingCatchment"] = catchments

    ids = identifiers(row)
    if ids:
        feature["schema:identifier"] = ids

    feature["schema:subjectOf"] = {
        "@type": "schema:Dataset",
        "schema:name": SOURCE_DATASET_NAME,
        "schema:url": clean(row.get("URL")) or SOURCE_URL,
        "schema:provider": {
            "@type": "schema:Organization",
            "schema:name": PROVIDER_NAME,
            "schema:url": PROVIDER_URL,
        },
    }

    properties = additional_properties(row, sarpid, linkable)
    if properties:
        feature["schema:additionalProperty"] = properties

    feature["gsp:hasGeometry"] = {
        "@type": "http://www.opengis.net/ont/sf#Point",
        "gsp:asWKT": {
            "@type": "http://www.opengis.net/ont/geosparql#wktLiteral",
            "@value": shapely.from_wkb(row["geometry"]).wkt,
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
def local_parquet(location: str) -> Iterator[Path]:
    """Yield a local path to the parquet cache, downloading it to a temp dir if remote"""
    if Path(location).exists():
        yield Path(location)
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "aquaticbarriers.parquet"
        download(location, path)
        yield path


def iter_rows(path: Path) -> Iterator[dict]:
    # Stream row groups so the ~990k wide rows are never all in memory at once
    for batch in pq.ParquetFile(path).iter_batches(batch_size=5000):
        yield from batch.to_pylist()


def linkable_ids(path: Path) -> frozenset[str]:
    """SARPIDs of every barrier that will be emitted, read from just two columns"""
    table = pq.read_table(path, columns=["SARPID", "geometry"])
    return frozenset(
        sarpid
        for sarpid, geometry in zip(
            table["SARPID"].to_pylist(), table["geometry"].to_pylist()
        )
        if geometry is not None
    )


def main(location: str) -> None:
    seen: set[str] = set()
    written = 0
    with local_parquet(location) as path:
        linkable = linkable_ids(path)
        for row in iter_rows(path):
            sarpid = row["SARPID"]
            if row["geometry"] is None:
                LOGGER.warning(f"Skipping {sarpid}: no geometry")
                continue
            if sarpid in seen:
                LOGGER.warning(
                    f"Skipping duplicate {sarpid} (OBJECTID {row['OBJECTID']})"
                )
                continue
            seen.add(sarpid)
            sys.stdout.write(json.dumps(row_to_jsonld(row, linkable), allow_nan=False))
            sys.stdout.write("\n")
            written += 1
    sys.stdout.flush()
    LOGGER.info(f"Wrote {written} features")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parquet",
        default=DEFAULT_PARQUET,
        help="Path or URL of the aquaticbarriers GeoParquet cache produced by crawl.py",
    )
    args = parser.parse_args()
    main(args.parquet)
