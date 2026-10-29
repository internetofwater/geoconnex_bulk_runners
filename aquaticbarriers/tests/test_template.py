# Copyright 2026 Lincoln Institute of Land Policy
# SPDX-License-Identifier: MIT

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from pyshacl import validate
from rdflib import Graph

import crawl
import template

SHAPE = Graph().parse(Path(__file__).parent / "geoconnex.ttl", format="turtle")

POINT = shapely.Point(-134.753646851, 58.144245148)


def make_dam(**overrides) -> dict:
    row = {
        "OBJECTID": 1,
        "BarrierType": "Dam",
        "Name": "Sand Pit Dam",
        "SARPID": "AK1111",
        "Source": "NID March19 2023",
        "SourceID": "AK00317",
        "NIDID": "AK00317",
        "River": None,
        "YearCompleted": 2016,
        "Height": 33,
        "Length": 0,
        "Width": 0,
        "StorageVolume": 0.0,
        "Hazard": "Low",
        "Construction": "Earthfill (Gravel, Sand, Silt, Clay)",
        "Purpose": "Flood Control",
        "Condition": "Fair",
        "Passability": "Unknown",
        "Removed": "no",
        "YearRemoved": 0,
        "Road": None,
        "RoadType": "Not applicable (dam)",
        "CrossingType": "Not applicable (dam)",
        "Constriction": "Not applicable (dam)",
        "BarrierSeverity": "Not applicable (dam)",
        "YearSurveyed": 0,
        "State": "Alaska",
        "County": "Juneau City and Borough",
        "HUC12": "190102040802",
        "NHDPlusID": -1.0,
        "UpstreamBarrierSARPID": "",
        "DownstreamBarrierSARPID": "",
        "UpstreamBarrierMiles": -1.0,
        "DownstreamBarrierMiles": -1.0,
        "TotalUpstreamMiles": -1.0,
        "FreeDownstreamMiles": -1.0,
        "MilesToOutlet": -1.0,
        "SourceLink": None,
        "URL": "https://tool.aquaticbarriers.org/report/combined_barriers/AK1111",
        "geometry": shapely.to_wkb(POINT),
    }
    row.update(overrides)
    return row


def make_crossing(**overrides) -> dict:
    row = {
        "OBJECTID": 629348,
        "BarrierType": "Surveyed road/stream crossing",
        "Name": "Surveyed road/stream crossing - Unnamed Trib Of Pedlar River",
        "SARPID": "sm100",
        "NIDID": None,
        "River": "Unnamed Trib Of Pedlar River",
        "YearCompleted": -1,
        "Height": -1,
        "Length": -1,
        "Width": -1,
        "Hazard": "Not applicable",
        "Construction": "Not applicable  (road/stream crossing)",
        "Purpose": "Not applicable (road/stream crossing)",
        "Passability": "Complete barrier",
        "RoadType": "Unpaved",
        "CrossingType": "Culvert",
        "Constriction": "Severe",
        "BarrierSeverity": "Complete barrier",
        "YearSurveyed": 2019,
        "HUC12": "020802030202",
    }
    row.update(overrides)
    return make_dam(**row)


def assert_conforms(doc: dict) -> None:
    graph = Graph().parse(data=json.dumps(doc), format="json-ld")
    conforms, _, report = validate(graph, shacl_graph=SHAPE)
    assert conforms, report


def properties(doc: dict) -> dict:
    return {
        p["schema:name"]: p["schema:value"] for p in doc["schema:additionalProperty"]
    }


def test_dam():
    doc = template.row_to_jsonld(make_dam())
    assert doc["@id"] == "https://geoconnex.us/nacc/barriers/AK1111"
    assert doc["schema:name"] == "Sand Pit Dam"
    assert doc["hyf:HydroLocationType"] == "dam"
    assert doc["hyf:containingCatchment"] == [
        {"@id": "https://geoconnex.us/ref/huc12/190102040802"},
        {"@id": "https://geoconnex.us/ref/hu10/1901020408"},
        {"@id": "https://geoconnex.us/ref/hu08/19010204"},
        {"@id": "https://geoconnex.us/ref/hu06/190102"},
        {"@id": "https://geoconnex.us/ref/hu04/1901"},
        {"@id": "https://geoconnex.us/ref/hu02/19"},
    ]
    assert doc["schema:identifier"] == [
        {
            "@type": "schema:PropertyValue",
            "schema:propertyID": "SARPID",
            "schema:value": "AK1111",
        },
        {
            "@type": "schema:PropertyValue",
            "schema:propertyID": "National Inventory of Dams ID",
            "schema:value": "AK00317",
        },
    ]
    assert doc["schema:description"] == (
        "Dam AK1111 in Juneau City and Borough, Alaska."
    )
    assert doc["schema:subjectOf"]["schema:url"] == (
        "https://tool.aquaticbarriers.org/report/combined_barriers/AK1111"
    )
    assert doc["gsp:hasGeometry"]["gsp:asWKT"]["@value"] == (
        "POINT (-134.753646851 58.144245148)"
    )
    values = properties(doc)
    assert values["Height"] == 33
    assert values["YearCompleted"] == 2016
    assert values["Hazard"] == "Low"
    assert_conforms(doc)


def test_missing_and_not_applicable_values_are_dropped():
    values = properties(template.row_to_jsonld(make_dam()))
    for key in ("Length", "Width", "StorageVolume", "YearRemoved", "RoadType", "River"):
        assert key not in values


def test_crossing():
    doc = template.row_to_jsonld(make_crossing())
    assert doc["@id"] == "https://geoconnex.us/nacc/barriers/sm100"
    assert doc["hyf:HydroLocationType"] == "culvert"
    assert doc["schema:description"].endswith("on Unnamed Trib Of Pedlar River.")
    values = properties(doc)
    assert values["CrossingType"] == "Culvert"
    assert values["BarrierSeverity"] == "Complete barrier"
    for key in ("Height", "Hazard", "Construction", "Purpose", "NIDID"):
        assert key not in values
    assert [i["schema:propertyID"] for i in doc["schema:identifier"]] == ["SARPID"]
    assert_conforms(doc)


@pytest.mark.parametrize(
    "crossing_type,expected",
    [
        ("Culvert", "culvert"),
        ("Bridge", "bridge"),
        ("Ford / low water crossing", "crossing"),
        ("Unknown", "crossing"),
        (None, "crossing"),
    ],
)
def test_crossing_location_type(crossing_type, expected):
    doc = template.row_to_jsonld(make_crossing(CrossingType=crossing_type))
    assert doc["hyf:HydroLocationType"] == expected


def test_dam_type_ignores_crossing_type():
    doc = template.row_to_jsonld(make_dam(CrossingType="Culvert"))
    assert doc["hyf:HydroLocationType"] == "dam"


def network_row(**overrides) -> dict:
    row = {
        "NHDPlusID": 75000100003933.0,
        "UpstreamBarrierSARPID": "sm778340",
        "DownstreamBarrierSARPID": "AK2",
        "UpstreamBarrierMiles": 0.282,
        "DownstreamBarrierMiles": 0.147,
        "TotalUpstreamMiles": 12.5,
        "FreeDownstreamMiles": 3.0,
        "MilesToOutlet": 5.995,
    }
    row.update(overrides)
    return make_dam(**row)


def network_properties(doc: dict) -> dict:
    return {p["schema:name"]: p for p in doc["schema:additionalProperty"]}


def test_network_links_only_to_barriers_in_the_dataset():
    doc = template.row_to_jsonld(network_row(), linkable={"AK1111", "sm778340", "AK2"})
    props = network_properties(doc)
    assert props["NearestUpstreamBarrier"]["schema:value"] == {
        "@id": "https://geoconnex.us/nacc/barriers/sm778340"
    }
    assert props["NearestDownstreamBarrier"]["schema:value"] == {
        "@id": "https://geoconnex.us/nacc/barriers/AK2"
    }
    assert_conforms(doc)


def test_network_links_to_missing_barriers_are_dropped():
    # e.g. waterfalls and filtered out small barriers are referenced but not in the data
    doc = template.row_to_jsonld(network_row(), linkable={"AK1111", "AK2"})
    props = network_properties(doc)
    assert "NearestUpstreamBarrier" not in props
    assert "NearestDownstreamBarrier" in props


def test_network_links_default_to_none():
    props = network_properties(template.row_to_jsonld(network_row()))
    assert "NearestUpstreamBarrier" not in props
    assert "NearestDownstreamBarrier" not in props


def test_network_link_never_points_at_itself():
    doc = template.row_to_jsonld(
        network_row(UpstreamBarrierSARPID="AK1111"), linkable={"AK1111", "AK2"}
    )
    assert "NearestUpstreamBarrier" not in network_properties(doc)


def test_network_distances_carry_units():
    props = network_properties(template.row_to_jsonld(network_row()))
    assert props["DownstreamBarrierMiles"]["schema:value"] == 0.147
    assert props["DownstreamBarrierMiles"]["schema:unitText"] == "miles"
    assert props["TotalUpstreamMiles"]["schema:unitText"] == "miles"
    assert props["NHDPlusID"]["schema:value"] == 75000100003933
    assert "schema:unitText" not in props["NHDPlusID"]


def test_off_network_distances_are_dropped():
    props = network_properties(template.row_to_jsonld(make_dam(Height=5)))
    for key in ("UpstreamBarrierMiles", "TotalUpstreamMiles", "MilesToOutlet"):
        assert key not in props


@pytest.mark.parametrize("name", [None, "", "   "])
def test_missing_name_gets_fallback(name):
    doc = template.row_to_jsonld(make_crossing(Name=name))
    assert doc["schema:name"] == "Surveyed road/stream crossing sm100"
    assert_conforms(doc)


def test_malformed_huc12_is_dropped():
    doc = template.row_to_jsonld(make_dam(HUC12="1901"))
    assert "hyf:containingCatchment" not in doc
    assert_conforms(doc)


def test_float_year_is_integer():
    doc = template.row_to_jsonld(make_dam(YearCompleted=2016.0))
    assert properties(doc)["YearCompleted"] == 2016


def test_unknown_barrier_type_raises():
    with pytest.raises(ValueError):
        template.row_to_jsonld(make_dam(BarrierType="Waterfall"))


def write_parquet(path: Path, rows: list[dict]) -> None:
    columns = {key: [row[key] for row in rows] for key in rows[0]}
    pq.write_table(pa.table(columns), path)


def test_main_skips_duplicates_and_missing_geometry(tmp_path, capsys):
    rows = [
        make_dam(DownstreamBarrierSARPID="sm100", UpstreamBarrierSARPID="AK2"),
        make_dam(OBJECTID=2, Name="duplicate"),
        make_dam(OBJECTID=3, SARPID="AK2", geometry=None),
        make_crossing(),
    ]
    parquet = tmp_path / "aquaticbarriers.parquet"
    write_parquet(parquet, rows)
    template.main(str(parquet))
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [doc["@id"] for doc in lines] == [
        "https://geoconnex.us/nacc/barriers/AK1111",
        "https://geoconnex.us/nacc/barriers/sm100",
    ]
    assert lines[0]["schema:name"] == "Sand Pit Dam"
    # sm100 is in the cache so is linked; AK2 has no geometry so is never emitted
    assert [p["schema:name"] for p in lines[0]["schema:additionalProperty"]].count(
        "NearestDownstreamBarrier"
    ) == 1
    assert "NearestUpstreamBarrier" not in json.dumps(lines[0])


LAYER = {
    "objectIdField": "OBJECTID",
    "fields": [
        {"name": "OBJECTID", "type": "esriFieldTypeOID"},
        {"name": "SARPID", "type": "esriFieldTypeString"},
        {"name": "Height", "type": "esriFieldTypeInteger"},
        {"name": "AnnualFlow", "type": "esriFieldTypeSingle"},
    ],
}


def fake_fetch(total: int):
    """Serve `total` features and honor the keyset `OBJECTID > n` where clause"""
    requests: list[dict] = []

    def fetch(url: str, params: dict) -> dict:
        requests.append(params)
        if params.get("returnCountOnly"):
            return {"count": total}
        if "outFields" not in params:
            return LAYER
        last = int(params["where"].rsplit(">", 1)[1])
        ids = [i for i in range(1, total + 1) if i > last][
            : params["resultRecordCount"]
        ]
        return {
            "features": [
                {
                    "geometry": {"type": "Point", "coordinates": [-100.0 + i, 40.0]},
                    "properties": {
                        "OBJECTID": i,
                        "SARPID": f"id{i}",
                        "Height": i,
                        "AnnualFlow": None if i % 2 else 1.5,
                    },
                }
                for i in ids
            ]
        }

    fetch.requests = requests  # type: ignore[attr-defined]
    return fetch


def test_crawl_pages_through_all_features_and_writes_geoparquet(tmp_path):
    out = tmp_path / "out.parquet"
    fetch = fake_fetch(5)
    assert crawl.crawl(out, fetch=fetch, page_size=2) == 5
    # 3 pages with data plus the empty page that ends the crawl
    pages = [r for r in fetch.requests if "outFields" in r]  # type: ignore[attr-defined]
    assert [r["where"] for r in pages] == [
        "OBJECTID > -1",
        "OBJECTID > 2",
        "OBJECTID > 4",
        "OBJECTID > 5",
    ]

    table = pq.read_table(out)
    assert table["OBJECTID"].to_pylist() == [1, 2, 3, 4, 5]
    assert table["AnnualFlow"].to_pylist() == [None, 1.5, None, 1.5, None]
    assert shapely.from_wkb(table["geometry"][0].as_py()).equals(
        shapely.Point(-99.0, 40.0)
    )
    geo = json.loads(pq.read_schema(out).metadata[b"geo"])
    assert geo["primary_column"] == "geometry"
    assert geo["columns"]["geometry"]["encoding"] == "WKB"
    assert not out.with_suffix(".parquet.partial").exists()


def test_unsupported_field_type_is_rejected():
    with pytest.raises(ValueError):
        crawl.build_schema({"fields": [{"name": "d", "type": "esriFieldTypeDate"}]})
