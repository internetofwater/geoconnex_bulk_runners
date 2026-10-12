# Copyright 2026 Lincoln Institute of Land Policy
# SPDX-License-Identifier: MIT

import json
from pathlib import Path

import pytest
from pyshacl import validate
from rdflib import Graph

import template

SHAPE = Graph().parse(Path(__file__).parent / "geoconnex.ttl", format="turtle")

COORDS = [-107.2826306, 35.9456833]


def make_props(**overrides) -> dict:
    props = {
        "FDCODE": 8,
        "FILL": 0,
        "PCTPOUR": 99.7814,
        "VPCTPOUR": 99.769,
        "DOWNHUC": "010802050502",
        "HUC_12": "010802050402",
        "HU_12_DS": "010802050505",
        "ISSINK": 0,
        "DOWNFLAG": 1,
        "TOHUC_DIST": 24.4703,
    }
    props.update(overrides)
    return props


def assert_conforms(doc: dict) -> None:
    graph = Graph().parse(data=json.dumps(doc), format="json-ld")
    conforms, _, report = validate(graph, shacl_graph=SHAPE)
    assert conforms, report


def test_full_row():
    doc = template.row_to_jsonld(make_props(), COORDS)
    assert doc["@id"] == "https://geoconnex.us/nhdplusv2/huc12/010802050402"
    assert doc["hyf:HydroLocationType"] == "outlet"
    assert doc["hyf:containingCatchment"] == {
        "@id": "https://geoconnex.us/ref/huc12/010802050402"
    }
    assert doc["hyf:lowerCatchment"] == {
        "@id": "https://geoconnex.us/ref/huc12/010802050505"
    }
    assert "hyf:outflow" not in doc
    assert doc["gsp:hasGeometry"]["gsp:asWKT"]["@value"] == (
        "POINT (-107.2826306 35.9456833)"
    )
    assert doc["schema:subjectOf"]["schema:provider"]["schema:name"] == (
        "U.S. Geological Survey"
    )
    values = {p["schema:name"]: p["schema:value"] for p in doc["schema:additionalProperty"]}
    assert values["FDCODE"] == 8
    assert values["DOWNHUC"] == "010802050502"
    assert_conforms(doc)


def test_sink_row():
    doc = template.row_to_jsonld(
        make_props(
            FDCODE=0,
            ISSINK=1,
            HU_12_DS="CLOSED BASIN",
            DOWNHUC="030801021000",
            HUC_12="030801021000",
        ),
        COORDS,
    )
    assert doc["hyf:HydroLocationType"] == "sink"
    assert "closed-basin sink" in doc["schema:description"]
    assert "hyf:lowerCatchment" not in doc
    assert "hyf:outflow" not in doc
    assert_conforms(doc)


@pytest.mark.parametrize(
    "raw,page",
    [
        ("OCEAN", "https://dbpedia.org/page/Ocean"),
        ("CANADA", "https://dbpedia.org/page/Canada"),
        ("MEXICO", "https://dbpedia.org/page/Mexico"),
        ("Mexico", "https://dbpedia.org/page/Mexico"),
    ],
)
def test_outflow(raw, page):
    doc = template.row_to_jsonld(make_props(HU_12_DS=raw), COORDS)
    assert doc["hyf:outflow"] == {"@id": page}
    assert "hyf:lowerCatchment" not in doc
    assert_conforms(doc)


def test_malformed_downstream_is_dropped(caplog):
    doc = template.row_to_jsonld(make_props(HU_12_DS="17040209XXXX"), COORDS)
    assert "hyf:lowerCatchment" not in doc
    assert "hyf:outflow" not in doc
    assert "Unrecognized HU_12_DS" in caplog.text
    assert_conforms(doc)


def test_blank_downstream_is_dropped_silently(caplog):
    doc = template.row_to_jsonld(make_props(HU_12_DS=None), COORDS)
    assert "hyf:lowerCatchment" not in doc
    assert "hyf:outflow" not in doc
    assert caplog.text == ""
    assert_conforms(doc)


def test_main_skips_duplicates_and_bad_geometry(tmp_path, capsys):
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": make_props(HUC_12="010802050402"),
                "geometry": {"type": "Point", "coordinates": COORDS},
            },
            {
                "type": "Feature",
                "properties": make_props(HUC_12="010802050402"),
                "geometry": {"type": "Point", "coordinates": COORDS},
            },
            {
                "type": "Feature",
                "properties": make_props(HUC_12="010802050403"),
                "geometry": None,
            },
            {
                "type": "Feature",
                "properties": make_props(HUC_12="010802050404"),
                "geometry": {"type": "Point", "coordinates": [-100.0, 40.0]},
            },
        ],
    }
    geojson = tmp_path / "fpp.geojson"
    geojson.write_text(json.dumps(collection))
    template.main(str(geojson))
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["@id"] for line in lines] == [
        "https://geoconnex.us/nhdplusv2/huc12/010802050402",
        "https://geoconnex.us/nhdplusv2/huc12/010802050404",
    ]
