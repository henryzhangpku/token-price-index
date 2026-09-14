"""The exported bundle must agree with the pipeline it was derived from.

The site is a reader, so the risk it carries is not a wrong price but a
*disagreeing* one: a page that recomputes something subtly different from
what the CLI prints would misrepresent the record while looking
authoritative. These tests pin the agreement, and pin that a withheld index
is exported as a decision with its reasons rather than as missing data.
"""

from __future__ import annotations

import json

from tokidx.pipeline import run_all
from tokidx.spec import (
    CONTRACTS,
    DEFAULT_GATES,
    MAX_TOTAL_ADJUSTMENT,
    METHODOLOGY_VERSION,
    TIER_WEIGHTS,
)
from tokidx.web import BUNDLE, build_bundle, write_bundle


def test_bundle_carries_what_the_site_reads():
    bundle = build_bundle()
    assert {
        "index_date", "prices_read", "collection_dates", "methodology_version",
        "observation_count", "disclaimer", "spec", "series", "indices",
    } <= set(bundle)
    assert bundle["methodology_version"] == METHODOLOGY_VERSION


def test_every_contract_appears_in_the_board_and_the_series():
    bundle = build_bundle()
    assert {i["code"] for i in bundle["indices"]} == set(CONTRACTS)
    assert set(bundle["series"]) == set(CONTRACTS)


def test_the_board_recomputes_the_published_value():
    """The headline property: the page's number is the pipeline's number."""
    bundle = build_bundle()
    fixings = run_all()
    for entry in bundle["indices"]:
        f = fixings[entry["code"]]
        assert entry["published"] == f.published
        assert entry["value"] == f.value
        assert entry["dispersion"] == f.dispersion


def test_a_withheld_index_keeps_its_reasons():
    """Dropping a withheld index would make withholding look like missing
    data. Exporting it next to the gate that stopped it is the argument."""
    bundle = build_bundle()
    withheld = [i for i in bundle["indices"] if not i["published"]]
    assert withheld, "the bundle should contain the refused frontier index"
    for entry in withheld:
        assert entry["value"] is None
        assert entry["withheld_reason"]
        assert any(not g["passed"] for g in entry["gates"])


def test_the_refusal_is_by_construction_not_by_data():
    frontier = next(i for i in build_bundle()["indices"] if i["code"] == "TIX-FRONTIER-OUT")
    assert frontier["indexable"] is False
    gate = next(g for g in frontier["gates"] if g["name"] == "indexable_good")
    assert gate["passed"] is False


def test_sellers_are_exported_in_price_order():
    for entry in build_bundle()["indices"]:
        prices = [p["price"] for p in entry["providers"]]
        assert prices == sorted(prices), entry["code"]


def test_series_has_one_entry_per_collection_date():
    bundle = build_bundle()
    for code, entries in bundle["series"].items():
        assert [e["index_date"] for e in entries] == bundle["collection_dates"], code
        for e in entries:
            assert (e["value"] is None) is (not e["published"])
            if not e["published"]:
                assert e["withheld_reason"]


def test_written_bundle_is_valid_json(tmp_path):
    path = write_bundle(tmp_path)
    assert path.name == BUNDLE
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == build_bundle()


def test_spec_publishes_the_constants_the_site_explains():
    spec = build_bundle()["spec"]
    assert spec["gates"]["min_providers"] == DEFAULT_GATES.min_providers
    assert spec["gates"]["max_dispersion"] == DEFAULT_GATES.max_dispersion
    assert spec["max_total_adjustment"] == MAX_TOTAL_ADJUSTMENT
    assert spec["tier_weights"] == {str(k): v for k, v in TIER_WEIGHTS.items()}
