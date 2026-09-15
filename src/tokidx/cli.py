"""Operator interface: run the fixing, and show the working behind it."""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .archive import append_to_tape, stamp_superseded, tape_row
from .models import Fixing
from .normalize import serving_check
from .pipeline import default_index_date, run, run_all
from .sources import all_observations, collected_at, latest_snapshot
from .spec import (
    CONTRACTS,
    DEFAULT_GATES,
    MAD_TO_SIGMA,
    MAX_TOTAL_ADJUSTMENT,
    OUTLIER_SIGMAS,
    SERVING_FACTORS,
    TIER_WEIGHTS,
)
from .store import Store

app = typer.Typer(add_completion=False, help="Token price benchmark reference implementation")
console = Console()


def _table(**kwargs) -> Table:
    return Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False, **kwargs)


@app.command()
def publish(
    index_date: str | None = typer.Option(None, "--date"),
    reason: str | None = typer.Option(
        None, "--reason", help="Why an existing fixing is being restated"
    ),
    db: Path | None = typer.Option(None, "--db", help="Store path; default is the working store"),
    tape: Path | None = typer.Option(None, "--tape", help="Tape path; default is data/tape.csv"),
) -> None:
    """Run every index, print the board, and write the fixings to the tape."""
    day = date.fromisoformat(index_date) if index_date else default_index_date()
    fixings = run_all(day)
    with Store(db) as store:
        rows, on_file = _record_all(store, fixings, day, reason)
    written = len(rows)
    if rows:
        # The tape is the durable publication record; the store is derived.
        # Written second, so a tape row never exists for a store write that
        # failed, and never rewritten -- a restatement is a new row.
        append_to_tape(rows, tape)
        stamp_superseded(tape)

    console.print(Panel(
        f"[bold]{day}[/]  ·  prices read {collected_at():%Y-%m-%d}  ·  "
        f"{len(all_observations())} observations",
        expand=False,
    ))

    t = _table(title="[bold]daily fixing[/]", title_justify="left")
    for col in ("index", "value", "prov", "obs", "disp"):
        t.add_column(col, justify="right" if col != "index" else "left", no_wrap=True)
    t.add_column("status", no_wrap=True)
    t.add_column("why", style="dim")
    for code, f in fixings.items():
        contributing = f.contributing
        failed = ", ".join(g.name.replace("_", " ") for g in f.gates if not g.passed)
        t.add_row(
            code,
            f"[bold cyan]{f.value:.3f}[/]" if f.published else "[dim]--[/]",
            str(len(contributing)),
            str(sum(p.quote_count for p in contributing)),
            f"{f.dispersion:.3f}" if f.dispersion is not None else "[dim]--[/]",
            "[green]published[/]" if f.published else "[yellow]withheld[/]",
            "" if f.published else failed,
        )
    console.print()
    console.print(t)

    withheld = [f for f in fixings.values() if not f.published]
    by_construction = [
        f for f in withheld
        if any(g.name == "indexable_good" and not g.passed for g in f.gates)
    ]
    console.print()
    console.print(
        f"  [dim]USD per million tokens. {len(withheld)} of {len(fixings)} indices "
        f"decline to print,[/]"
    )
    console.print(
        f"  [dim]and {len(by_construction)} of those cannot print by construction "
        "rather than for want of data.[/]"
    )
    if written:
        console.print(f"  [dim]tape: {written} fixing(s) recorded for {day}"
                      f"{' as a restatement' if reason else ''}.[/]")
    if on_file:
        console.print(f"  [dim]tape: {on_file} fixing(s) for {day} already on file; "
                      "pass --reason to restate them.[/]")
    console.print()


def _record_all(
    store: Store, fixings: dict[str, Fixing], day: date, reason: str | None
) -> tuple[list[dict], int]:
    """Write each fixing to the store as a new revision, or leave it alone.

    The first write for a date needs no reason. A second write of the same
    date is a restatement and must say why, so a bare re-run of ``publish``
    leaves the record untouched rather than manufacturing revisions -- the
    store enforces that, and this decides before asking it to.

    Returns the tape rows for what was written, each naming the snapshot it
    came from, and the count left alone.
    """
    snapshot = latest_snapshot().name
    run_id = store.start_run(collected_at(), len(all_observations()), {"snapshot": snapshot})
    rows: list[dict] = []
    on_file = 0
    for f in fixings.values():
        if store.latest(f.index_code, day) is not None and not reason:
            on_file += 1
            continue
        store.record(f, run_id=run_id, revision_reason=reason)
        rows.append(tape_row(store.latest(f.index_code, day), snapshot))
    return rows, on_file


@app.command()
def explain(index_code: str = typer.Argument(...),
            index_date: str | None = typer.Option(None, "--date")) -> None:
    """Walk one fixing from published prices to the decision."""
    day = date.fromisoformat(index_date) if index_date else default_index_date()
    if index_code not in CONTRACTS:
        # A mistyped or renamed code is the likeliest way to arrive here, so say
        # what does exist rather than raising KeyError through the traceback.
        console.print(f"\n[yellow]no contract {index_code!r}[/]")
        console.print(f"[dim]try one of: {', '.join(CONTRACTS)}[/]\n")
        raise typer.Exit(2)
    contract = CONTRACTS[index_code]
    f = run(index_code, day)

    console.print(Panel(
        f"[bold]{index_code}[/]  ·  {contract.display_name}  ·  {day}\n"
        f"[dim]1M {contract.direction.value} tokens of {contract.model}, "
        f"{contract.serving.value} serving, {contract.context_tokens:,} context, "
        f"{contract.region.upper()}. Priced in {contract.unit}.[/]",
        expand=False,
    ))

    # -- 1. is this good even indexable ------------------------------------
    console.rule("[bold cyan]1[/]  [bold]Is a benchmark coherent here?[/]",
                 align="left", style="cyan")
    if contract.is_indexable:
        console.print("  Open weights, served by several independent sellers, so the "
                      "same good\n  has competing prices. An index measures something.\n")
    else:
        console.print("  [yellow]Proprietary model: exactly one seller.[/]\n"
                      "  [dim]There is no cross-seller price to discover. An average of "
                      "one seller's\n  list price is that seller's list price with an "
                      "index's name on it, and\n  publishing it would lend a private "
                      "pricing decision the authority of a\n  market rate.[/]\n")

    # -- 2. restatement -----------------------------------------------------
    console.rule("[bold cyan]2[/]  [bold]Restating to the benchmark good[/]",
                 align="left", style="cyan")
    if f.rejections:
        by_reason: dict[str, int] = {}
        for r in f.rejections:
            by_reason[r.reason] = by_reason.get(r.reason, 0) + 1
        rt = _table(show_header=False)
        rt.add_column("reason", no_wrap=True)
        rt.add_column("n", justify="right")
        for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            rt.add_row(reason.replace("_", " "), str(n))
        console.print(rt)
    console.print(f"  [dim]{len(f.rejections)} observations discarded, "
                  f"{sum(p.quote_count for p in f.providers)} restated. Cumulative "
                  f"adjustment is capped at {MAX_TOTAL_ADJUSTMENT}x.[/]\n")

    # -- 3. providers, screen, weights --------------------------------------
    console.rule("[bold cyan]3[/]  [bold]Sellers, screen and weights[/]",
                 align="left", style="cyan")
    if f.providers:
        pt = _table()
        pt.add_column("provider", no_wrap=True)
        pt.add_column("price", justify="right")
        pt.add_column("quotes", justify="right")
        pt.add_column("tier", justify="right")
        pt.add_column("weight", justify="right")
        pt.add_column("share", justify="right")
        total = sum(p.weight for p in f.contributing)
        for p in sorted(f.providers, key=lambda p: p.price):
            share = f"{p.weight / total:.1%}" if total > 0 and not p.screened_out else "--"
            pt.add_row(
                f"[dim]{p.provider}[/]" if p.screened_out else p.provider,
                f"{p.price:.3f}",
                str(p.quote_count),
                str(int(p.best_tier)),
                f"{p.weight:.2f}" if not p.screened_out else "[dim]--[/]",
                share,
            )
        console.print(pt)
        prices = [p.price for p in f.contributing]
        if len(prices) >= 3:
            med = statistics.median(prices)
            mad = statistics.median([abs(x - med) for x in prices])
            console.print(f"  [dim]median {med:.3f} · MAD {mad:.3f} · "
                          f"x{MAD_TO_SIGMA} = {mad * MAD_TO_SIGMA:.3f} · "
                          f"keep band {med - OUTLIER_SIGMAS * mad * MAD_TO_SIGMA:.3f} "
                          f".. {med + OUTLIER_SIGMAS * mad * MAD_TO_SIGMA:.3f}[/]")
    else:
        console.print("  [dim]no sellers survived restatement[/]")
    console.print()

    # -- 4. gates ------------------------------------------------------------
    console.rule("[bold cyan]4[/]  [bold]Publication gates[/]", align="left", style="cyan")
    gt = _table()
    gt.add_column(" ", no_wrap=True)
    gt.add_column("gate", no_wrap=True)
    gt.add_column("detail")
    for g in f.gates:
        gt.add_row("[bold green]PASS[/]" if g.passed else "[bold red]FAIL[/]",
                   g.name.replace("_", " "),
                   g.detail if g.passed else f"[bold red]{g.detail}[/]")
    console.print(gt)
    console.print()
    if f.published:
        console.print(Panel(f"every gate cleared  ->  [bold]{f.value:.3f}[/] "
                            f"USD per million tokens", border_style="green", expand=False))
    else:
        console.print(Panel(f"[bold yellow]withheld[/]\n[dim]{f.withheld_reason}[/]",
                            border_style="yellow", expand=False))
    if f.flags:
        console.print("\n[dim]flags[/]")
        for fl in f.flags:
            console.print(f"  [{'yellow' if fl.severity == 'warn' else 'dim'}]"
                          f"{fl.code:32}[/] {fl.detail}")
    console.print()


@app.command()
def contracts() -> None:
    """The benchmark goods, the weights, and the gates."""
    t = _table(title="[bold]benchmark contracts[/]", title_justify="left")
    for col in ("code", "good", "direction", "sellers", "indexable"):
        t.add_column(col)
    for c in CONTRACTS.values():
        t.add_row(c.code, c.model, c.direction.value,
                  "many" if c.open_weights else "[yellow]one[/]",
                  "[green]yes[/]" if c.is_indexable else "[yellow]no[/]")
    console.print()
    console.print(t)

    w = _table(title="[bold]evidence tiers[/]", title_justify="left", show_header=False)
    w.add_column("tier", no_wrap=True)
    w.add_column("weight", justify="right")
    w.add_column("meaning", style="dim")
    w.add_row("1  list", f"{TIER_WEIGHTS[1]:.2f}", "published rate card, transactable today")
    w.add_row("2  aggregated", f"{TIER_WEIGHTS[2]:.2f}", "seen through a router, margin unknown")
    w.add_row("3  curated", f"{TIER_WEIGHTS[3]:.2f}", "administrator's sourced entry")
    console.print(w)

    s = _table(title="[bold]serving restatement[/]", title_justify="left", show_header=False)
    s.add_column("serving", no_wrap=True)
    s.add_column("factor", justify="right")
    for serving, factor in SERVING_FACTORS.items():
        s.add_row(serving.value, f"x{factor:.2f}")
    console.print(s)

    g = _table(title="[bold]publication gates[/]", title_justify="left", show_header=False)
    g.add_column("gate", no_wrap=True)
    g.add_column("value", justify="right")
    g.add_row("indexable good", "required")
    g.add_row("min providers", str(DEFAULT_GATES.min_providers))
    g.add_row("min observations", str(DEFAULT_GATES.min_observations))
    g.add_row("max dispersion", f"{DEFAULT_GATES.max_dispersion}")
    g.add_row("max provider share", f"{DEFAULT_GATES.max_provider_weight_share:.0%}")
    console.print(g)
    console.print()


@app.command()
def calibrate() -> None:
    """Test the serving factors against what sellers actually charge."""
    console.print(Panel(
        "Where one seller publishes the same good two ways, the ratio between\n"
        "the prices is a direct observation of what it charges for the\n"
        "difference -- not a guess. That is the only calibration this schedule\n"
        "has, and it covers two of its four factors.",
        expand=False,
    ))
    evidence = serving_check(all_observations())
    if not evidence:
        console.print("\n[yellow]no seller publishes the same good two ways[/]\n")
        raise typer.Exit(0)

    t = _table(title="[bold]observed vs asserted[/]", title_justify="left")
    for col in ("seller", "good", "observed", "asserted", "gap"):
        t.add_column(col, justify="right" if col != "seller" and col != "good" else "left")
    for provider, good, observed in evidence:
        serving_name = good.rsplit(" ", 1)[-1]
        asserted = next(
            (f for s, f in SERVING_FACTORS.items() if s.value == serving_name), None
        )
        gap = f"{observed / asserted - 1:+.1%}" if asserted else "--"
        t.add_row(provider, good, f"{observed:.2f}",
                  f"{asserted:.2f}" if asserted else "--", gap)
    console.print()
    console.print(t)
    console.print("  [dim]A ratio identical across every SKU of one seller is a discount\n"
                  "  policy, not a market spread, and carries no information. Two\n"
                  "  independent sellers publishing the same ratio is a convention.[/]\n")


@app.command("export-web")
def export_web(
    out: str = typer.Option("web/data", help="Directory to write the bundle into"),
    index_date: str | None = typer.Option(None, "--date"),
) -> None:
    """Write the JSON the static site reads.

    The site never recomputes anything: it renders what this pipeline decided,
    so the page and the CLI cannot disagree.
    """
    from pathlib import Path

    from .web import write_bundle

    day = date.fromisoformat(index_date) if index_date else default_index_date()
    path = write_bundle(Path(out), day)
    console.print(f"  wrote [bold]{path}[/]  ({path.stat().st_size:,} bytes)")


@app.command()
def collect(
    out: str | None = typer.Option(None, help="Directory for snapshots"),
) -> None:
    """Read every seller's price once, and write down exactly what was seen.

    Collection is separate from estimation on purpose. A pipeline that fetches
    and computes in one pass can never show that a past value follows from its
    own inputs, because those inputs are gone by the time anyone asks.
    """
    from datetime import UTC, datetime
    from pathlib import Path

    from .collect import collect as fetch
    from .collect import venue_breakdown
    from .sources import SnapshotExists, write_snapshot

    now = datetime.now(UTC)
    with console.status("reading sellers..."):
        observations, venues = fetch(now)

    if not observations:
        for venue in venues:
            console.print(f"[yellow]{venue.venue}: {venue.detail}[/]")
        console.print("[yellow]nothing collected; no snapshot written[/]")
        raise typer.Exit(1)

    try:
        path = write_snapshot(
            observations, now,
            venues=[v.__dict__ for v in venues],
            directory=Path(out) if out else None,
        )
    except SnapshotExists as exc:
        # Not a failure: the day is already on file and the rest of the daily
        # run -- rebuild, publish, verify -- is idempotent against it. Exit
        # cleanly so a re-dispatched run proceeds rather than aborting.
        console.print(f"[yellow]already collected today: {exc}[/]")
        console.print("[dim]nothing written; the existing snapshot stands[/]")
        raise typer.Exit(0) from None

    t = _table(title="[bold]collected[/]", title_justify="left")
    for col in ("venue", "sellers", "observations", "note"):
        t.add_column(col, justify="right" if col in ("sellers", "observations") else "left")
    breakdown = venue_breakdown(observations)
    for venue in venues:
        t.add_row(venue.venue, str(breakdown.get(venue.venue, 0)),
                  str(venue.observations), venue.detail)
    console.print()
    console.print(t)

    # The number that matters more than the seller count. A hundred sellers
    # behind one venue is one source of failure, not a hundred.
    if len(breakdown) == 1:
        only = next(iter(breakdown))
        console.print()
        console.print(
            f"  [yellow]every observation came through one venue ({only}).[/]"
        )
        console.print(
            "  [dim]seller count measures the market; venue count measures the sample.[/]"
        )
    console.print()
    console.print(f"  wrote [bold]{path}[/]")
    console.print()



if __name__ == "__main__":
    app()


@app.command()
def sensitivity(index_date: str | None = typer.Option(None, "--date")) -> None:
    """Measure how much of each fixing rests on the restatement schedule.

    Section 4 of the methodology concedes its factors are judgement and bounds
    them. This answers the question that concession leaves open: how far does
    the fixing actually move because of them?
    """
    from .normalize import normalize_all
    from .sensitivity import exposure_all

    day = date.fromisoformat(index_date) if index_date else default_index_date()
    observations = all_observations()
    quotes_by_code = {code: normalize_all(observations, code)[0] for code in CONTRACTS}
    rows = exposure_all(day, quotes_by_code, DEFAULT_GATES)

    console.print(Panel(
        "The counterfactual recomputes each fixing from quotes that conformed to\n"
        "the contract as observed -- standard serving, benchmark context -- and\n"
        "discards every restated one. It is not a better estimate: it throws away\n"
        "part of the sample and leans on whichever sellers publish the benchmark\n"
        "configuration natively. It measures dependence on judgement, nothing else.",
        title=f"restatement exposure · {day}",
        expand=False,
    ))

    t = _table()
    for col in ("index", "published", "conforming only", "shift", "conforming",
                "adj weight", "still publishable"):
        t.add_column(col, justify="left" if col == "index" else "right", no_wrap=True)
    for row in rows:
        t.add_row(
            row.index_code,
            f"{row.published:.3f}" if row.published is not None else "[dim]--[/]",
            f"{row.conforming_only:.3f}" if row.conforming_only is not None else "[dim]--[/]",
            f"{row.shift:+.1%}" if row.shift is not None else "[dim]--[/]",
            f"{row.conforming_quotes}/{row.total_quotes}",
            f"{row.weight_share_adjusted:.0%}",
            "[green]yes[/]" if row.publishable_without_adjustment else "[yellow]no[/]",
        )
    console.print(t)

    factors: dict[str, float] = {}
    for row in rows:
        for name, share in row.by_factor.items():
            factors[name] = max(factors.get(name, 0.0), share)
    if factors:
        console.print("\n  [bold]share of quotes touched by each factor, highest across indices[/]")
        for name, share in sorted(factors.items(), key=lambda kv: -kv[1]):
            console.print(f"    {name:10} {share:.0%}")
    console.print()


@app.command()
def rebuild(
    db: Path | None = typer.Option(None, "--db"),
    tape: Path | None = typer.Option(None, "--tape"),
) -> None:
    """Reconstruct the store from the tape and the snapshots.

    The store is derived state and is never committed; this is how a fresh
    checkout, or the daily runner, gets its history back. Values come from
    the tape with their revision numbers intact -- nothing is recomputed. A
    date that has a snapshot and no tape row is backfilled and marked as such.
    """
    from .reproduce import coverage
    from .reproduce import rebuild as _rebuild

    with Store(db) as store:
        restored, backfilled = _rebuild(store, tape)
    stats = coverage(tape)
    console.print(Panel(
        f"restored    {restored} tape row(s)\n"
        f"backfilled  {backfilled} row(s) for dates the tape had never seen\n"
        f"tape        {stats['tape_rows']} row(s) across {stats['index_dates']} date(s), "
        f"{stats['snapshots']} snapshot(s) on file",
        title="rebuild", expand=False,
    ))


@app.command()
def verify(tape: Path | None = typer.Option(None, "--tape")) -> None:
    """Recompute every published value from its archived inputs and compare.

    Exits non-zero on any mismatch, so CI fails when the series stops being
    reproducible from its own archive.
    """
    from .reproduce import verify as _verify

    report = _verify(tape)
    console.print(Panel(
        f"checked      {report.checked}\n"
        f"reproduced   {report.matched}\n"
        f"mismatched   {len(report.mismatches)}\n"
        f"unverifiable {len(report.unverifiable)}\n"
        f"dangling     {len(report.dangling)}",
        title="verify", expand=False,
    ))
    for note in report.dangling:
        console.print(f"  [bold red]dangling[/] {note}")
    for note in report.methodology_drift:
        console.print(f"  [yellow]methodology[/] {note}")
    for item in report.unverifiable:
        console.print(f"  [dim]unverifiable[/] {item.index_code} {item.index_date}: {item.detail}")
    for item in report.mismatches:
        pub = f"{item.published:.4f}" if item.published is not None else "--"
        rec = f"{item.recomputed:.4f}" if item.recomputed is not None else "--"
        console.print(f"  [bold red]mismatch[/] {item.index_code} {item.index_date}: "
                      f"published {pub}, recomputed {rec} ({item.detail})")
    if not report.ok:
        raise typer.Exit(1)
    console.print("[green]series reproduces from its archive[/]")


@app.command()
def show(
    index_code: str = typer.Argument(...),
    db: Path | None = typer.Option(None, "--db"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Print the current view of one series from the store."""
    if index_code not in CONTRACTS:
        console.print(f"\n[yellow]no contract {index_code!r}[/]")
        console.print(f"[dim]try one of: {', '.join(CONTRACTS)}[/]\n")
        raise typer.Exit(2)
    with Store(db) as store:
        rows = store.history(index_code, limit)
    if not rows:
        console.print(f"[yellow]no values for {index_code} on the tape; run rebuild[/]")
        raise typer.Exit(1)
    contract = CONTRACTS[index_code]
    t = _table(title=f"[bold]{index_code}[/]  ·  {contract.display_name}", title_justify="left")
    for col in ("date", "value", "rev", "prov", "obs", "disp", "status"):
        t.add_column(col, justify="right" if col != "status" else "left", no_wrap=True)
    for row in rows:
        published = row["status"] == "published"
        t.add_row(
            row["index_date"],
            f"[bold cyan]{row['value']:.3f}[/]" if published else "[dim]--[/]",
            str(row["revision"]),
            str(row["provider_count"]),
            str(row["observation_count"]),
            f"{row['dispersion']:.3f}" if row["dispersion"] is not None else "[dim]--[/]",
            "[green]published[/]" if published else "[yellow]withheld[/]",
        )
    console.print()
    console.print(t)
    console.print()


@app.command("as-of")
def as_of(
    index_code: str = typer.Argument(...),
    index_date: str = typer.Argument(..., help="Index date (event time)"),
    knowledge_time: str = typer.Argument(..., help="ISO timestamp (knowledge time)"),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """Answer: what did we say for this date, as known at that moment?"""
    when = datetime.fromisoformat(knowledge_time)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    with Store(db) as store:
        row = store.as_of(index_code, date.fromisoformat(index_date), when)
    if row is None:
        console.print(f"[yellow]{index_code} for {index_date} was not yet on the tape "
                      f"as of {when.isoformat()}[/]")
        raise typer.Exit(1)
    value = f"{row['value']:.4f}" if row["value"] is not None else "--"
    console.print(Panel(
        f"value      {value}\n"
        f"status     {row['status']}\n"
        f"revision   {row['revision']}\n"
        f"published  {row['published_at']}\n"
        f"superseded {row['superseded_at'] or 'no -- this was still the live value'}",
        title=f"{index_code} {index_date} as known at {when.isoformat()}",
        expand=False,
    ))


@app.command()
def revisions(
    index_code: str = typer.Argument(...),
    index_date: str = typer.Argument(...),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """List every revision of one value, including superseded ones."""
    with Store(db) as store:
        rows = store.revisions(index_code, date.fromisoformat(index_date))
    if not rows:
        console.print("[yellow]no revisions on the tape[/]")
        raise typer.Exit(1)
    t = _table()
    for col in ("rev", "value", "status", "published_at", "superseded_at", "reason"):
        t.add_column(col, no_wrap=col != "reason")
    for row in rows:
        t.add_row(
            str(row["revision"]),
            f"{row['value']:.4f}" if row["value"] is not None else "--",
            row["status"],
            row["published_at"],
            row["superseded_at"] or "[green]live[/]",
            row["revision_reason"] or "",
        )
    console.print(t)
