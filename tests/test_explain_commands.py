"""The commands must agree with the estimator they narrate.

``explain`` exists to be read aloud: it prints the arithmetic behind one
fixing so that a reader can follow the decision rather than trust it. That
makes its failure mode unusual. A wrong number here is not a wrong index --
the index is computed elsewhere -- it is a *misleading explanation* of a
correct one, which is worse, because it is the artefact people are invited to
check the index against.

So the properties pinned are about accounting. ``explain`` must name every
seller the estimator saw, kept and screened alike, and must show the gate
that refused a withheld fixing. And the commands a reader is told to type
must exit cleanly, because a traceback on the README's first example is the
defect that reads worst.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from tokidx import cli
from tokidx.pipeline import run, run_all
from tokidx.spec import CONTRACTS

#: Wide enough that rich never crops a seller name out of the assertions.
WIDE = {"COLUMNS": "200", "TERM": "dumb", "PYTHONIOENCODING": "utf-8"}


def _run(*args: str):
    return CliRunner().invoke(cli.app, list(args), env=WIDE)


def test_publish_prints_every_contract():
    result = _run("publish")
    assert result.exit_code == 0, result.output
    for code in CONTRACTS:
        assert code in result.output


def test_publish_counts_the_withheld_honestly():
    result = _run("publish")
    withheld = sum(1 for f in run_all().values() if not f.published)
    assert f"{withheld} of {len(CONTRACTS)} indices" in result.output


@pytest.mark.parametrize("code", list(CONTRACTS))
def test_explain_exits_cleanly_for_every_contract(code):
    result = _run("explain", code)
    assert result.exit_code == 0, result.output
    assert code in result.output


def test_explain_accounts_for_every_seller():
    """Kept plus screened must equal the panel. An explanation that hides its
    own casualties is indistinguishable from silently deleting them."""
    code = "TIX-GLM53-OUT"
    result = _run("explain", code)
    assert result.exit_code == 0, result.output
    for p in run(code).providers:
        assert p.provider in result.output, f"{p.provider} missing from the sellers table"


def test_explain_shows_the_gate_that_refused():
    result = _run("explain", "TIX-FRONTIER-OUT")
    assert result.exit_code == 0, result.output
    assert "FAIL" in result.output
    assert "indexable good" in result.output


def test_explain_refuses_an_unknown_code_without_a_traceback():
    """The README's first example once raised KeyError through the stack.
    A mistyped or renamed code is the likeliest way to arrive here."""
    result = _run("explain", "TIX-NOPE-OUT")
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "no contract" in result.output
    for code in CONTRACTS:
        assert code in result.output


def test_contracts_lists_every_good():
    result = _run("contracts")
    assert result.exit_code == 0, result.output
    for code in CONTRACTS:
        assert code in result.output


def test_calibrate_runs():
    result = _run("calibrate")
    assert result.exit_code == 0, result.output


def test_sensitivity_reports_every_contract():
    result = _run("sensitivity")
    assert result.exit_code == 0, result.output
    for code in CONTRACTS:
        assert code in result.output


def test_export_web_writes_the_bundle(tmp_path):
    result = _run("export-web", "--out", str(tmp_path))
    assert result.exit_code == 0, result.output
    bundle = json.loads((tmp_path / "fixings.json").read_text(encoding="utf-8"))
    assert {i["code"] for i in bundle["indices"]} == set(CONTRACTS)
