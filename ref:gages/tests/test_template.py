# Copyright 2026 Lincoln Institute of Land Policy
# SPDX-License-Identifier: MIT

import json
import sqlite3
import struct
from pathlib import Path

import pytest
import shapely
from pyshacl import validate
from rdflib import Graph

import template

SHAPE = Graph().parse(Path(__file__).parent / "geoconnex.ttl", format="turtle")


def gpkg_blob(geometry: shapely.Geometry, envelope: bool = False) -> bytes:
    flags = 0b0000_0001  # little endian, no envelope
    extra = b""
    if envelope:
        flags |= 1 << 1
        extra = struct.pack("<4d", *shapely.bounds(geometry))
    header = b"GP" + bytes([0, flags]) + struct.pack("<i", 4269)
    return header + extra + shapely.to_wkb(geometry)


def make_row(**overrides) -> dict:
    row = {
        "fid": 1,
        "geom": gpkg_blob(shapely.Point(-107.2826306, 35.9456833)),
        "id": 1000001,
        "uri": "https://geoconnex.us/ref/gages/1000001",
        "name": "PENISTAJA TRIB UNTREATED NEAR TORREON, NM",
        "description": "USGS NWIS Stream/River/Lake Site USGS-08332622: PENISTAJA TRIB UNTREATED NEAR TORREON, NM",
        "subjectof": "https://waterdata.usgs.gov/monitoring-location/USGS-08332622",
        "provider": "https://waterdata.usgs.gov",
        "provider_id": "USGS-08332622",
        "nhdpv2_reachcode": "13020205000216",
        "nhdpv2_reach_measure": 80.3326,
        "mainstem_uri": "https://geoconnex.us/ref/mainstems/1622734",
        "nws_url": "https://water.noaa.gov/gauges/MCDM2",
    }
    row.update(overrides)
    return row


def assert_conforms(doc: dict) -> None:
    graph = Graph().parse(data=json.dumps(doc), format="json-ld")
    conforms, _, report = validate(graph, shacl_graph=SHAPE)
    assert conforms, report


@pytest.mark.parametrize("envelope", [False, True])
def test_gpkg_geometry(envelope: bool):
    point = shapely.Point(-100.5, 40.25)
    assert template.gpkg_geometry(gpkg_blob(point, envelope)).equals(point)


def test_full_row():
    doc = template.row_to_jsonld(make_row())
    assert doc["@id"] == "https://geoconnex.us/ref/gages/1000001"
    assert doc["schema:provider"] == "https://waterdata.usgs.gov"
    dataset, nws = doc["schema:subjectOf"]
    assert dataset["schema:provider"]["schema:name"] == "USGS"
    assert nws["schema:url"] == "https://water.noaa.gov/gauges/MCDM2"
    reach, mainstem = doc["hyf:referencedPosition"]
    position = reach["hyf:HY_IndirectPosition"]
    assert position["hyf:distanceExpression"]["hyf:HY_DistanceFromReferent"] == {
        "hyf:interpolative": 80.3326
    }
    assert position["hyf:linearElement"] == {
        "@id": "https://geoconnex.us/nhdplusv2/reachcode/13020205000216"
    }
    assert mainstem["hyf:HY_IndirectPosition"]["hyf:linearElement"] == {
        "@id": "https://geoconnex.us/ref/mainstems/1622734"
    }
    assert (
        doc["gsp:hasGeometry"]["gsp:asWKT"]["@value"]
        == "POINT (-107.2826306 35.9456833)"
    )
    assert_conforms(doc)


def test_minimal_row():
    doc = template.row_to_jsonld(
        make_row(
            name=None,
            description=None,
            subjectof=None,
            nws_url=None,
            nhdpv2_reachcode=None,
            nhdpv2_reach_measure=None,
            mainstem_uri=None,
        )
    )
    assert doc["schema:name"] == "USGS-08332622"
    for key in ("schema:description", "schema:subjectOf", "hyf:referencedPosition"):
        assert key not in doc
    assert_conforms(doc)


def test_reach_without_measure():
    doc = template.row_to_jsonld(make_row(nhdpv2_reach_measure=None, mainstem_uri=None))
    (reach,) = doc["hyf:referencedPosition"]
    assert reach == {
        "hyf:HY_IndirectPosition": {
            "hyf:linearElement": {
                "@id": "https://geoconnex.us/nhdplusv2/reachcode/13020205000216"
            }
        }
    }


def test_escaped_percent_removed():
    doc = template.row_to_jsonld(make_row(name="COLUMN 2-75\\\\\\% SH"))
    assert doc["schema:name"] == "COLUMN 2-75 SH"


def test_non_usgs_provider():
    doc = template.row_to_jsonld(make_row(provider="https://cdec.water.ca.gov"))
    provider = doc["schema:subjectOf"][0]["schema:provider"]
    assert provider["schema:name"] == "California Department of Water Resources"
    assert_conforms(doc)


def test_main_skips_duplicates(tmp_path, capsys):
    gpkg = tmp_path / "ref_gages.gpkg"
    rows = [
        make_row(fid=1),
        make_row(fid=2, name="duplicate"),
        make_row(fid=3, id=1000002, uri="https://geoconnex.us/ref/gages/1000002"),
    ]
    columns = list(rows[0])
    with sqlite3.connect(gpkg) as conn:
        conn.execute(f"CREATE TABLE ref_gages ({', '.join(columns)})")
        conn.executemany(
            f"INSERT INTO ref_gages VALUES ({', '.join('?' * len(columns))})",
            [list(r.values()) for r in rows],
        )
    template.main(str(gpkg))
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["@id"] for line in lines] == [
        "https://geoconnex.us/ref/gages/1000001",
        "https://geoconnex.us/ref/gages/1000002",
    ]
    assert json.loads(lines[0])["schema:name"] != "duplicate"
