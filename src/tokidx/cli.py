"""Operator interface: run the fixing, and show the working behind it."""

from __future__ import annotations

import statistics
from datetime import date

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .normalize import serving_check
from .pipeline import default_index_date, run, run_all
from .sources import all_observations, collected_at
from .spec import (
    CONTRACTS,
    DEFAULT_GATES,
    MAD_TO_SIGMA,
    MAX_TOTAL_ADJUSTMENT,
    OUTLIER_SIGMAS,
    SERVING_FACTORS,
    TIER_WEIGHTS,
)

app = typer.Typer(add_completion=False, help="Token price benchmark reference implementation")
console = Console()


def _table(**kwargs) -> Table:
    return Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False, **kwargs)


@app.command()
def publish(index_date: str | None = typer.Option(None, "--date")) -> None:
    """Run every index and print the board."""
    day = date.fromisoformat(index_date) if index_date else default_index_date()
    fixings = run_all(day)

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
    console.print()


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
    from .sources import write_snapshot

    now = datetime.now(UTC)
    with console.status("reading sellers..."):
        observations, venues = fetch(now)

    if not observations:
        for venue in venues:
            console.print(f"[yellow]{venue.venue}: {venue.detail}[/]")
        console.print("[yellow]nothing collected; no snapshot written[/]")
        raise typer.Exit(1)

    path = write_snapshot(
        observations, now,
        venues=[v.__dict__ for v in venues],
        directory=Path(out) if out else None,
    )

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
