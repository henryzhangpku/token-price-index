# Related work

What has been written about token and compute derivatives, and how this index
relates to it. Cited where a paper corroborates a result or names an object this
repository turns out to build — never as borrowed authority.

---

## Xing (2026), *AI Token Futures Market: Commoditization of Compute and Derivatives Contract Design*

arXiv:[2603.21690](https://arxiv.org/abs/2603.21690) · Yicai Xing · March 2026

The closest thing to a specification for the market this index would serve. It
argues that AI inference tokens meet the criteria for a commodity and then
designs the contract: a **Standard Inference Token**, margin requirements, a
market-maker regime, and cash settlement against a **Token Price Index**.

### The part worth recording

**The paper assumes a Token Price Index exists. This repository builds one.**

Its settlement design needs a published, reproducible per-token reference rate.
The `TIX-*` series here — an immutable snapshot per collection date, an
append-only tape, and every value reconstructible from its own inputs — is that
object, built independently and without reference to this paper. A contract
design and a settlement input arrived at the same interface from opposite
directions.

That is the useful observation, and it is also the limit of the connection: the
paper says nothing about *how* such an index should be estimated, screened or
gated, which is the entire content of [`../METHODOLOGY.md`](../METHODOLOGY.md).

### It also shares this index's analogy

Xing compares token markets to **electricity and carbon credits**, and derives
supply from energy cost, hardware efficiency and algorithmic efficiency. The same
family this benchmark's sibling reaches for when it refuses to publish a forward
curve: a non-storable good prices like power, not like metal. See
[`gpu-price-index/src/gpuidx/forward.py`](https://github.com/henryzhangpku/gpu-price-index/blob/main/src/gpuidx/forward.py).

### Read with its limits in view

Single independent author. **No empirical data**: the headline result — that
hedging reduces enterprise compute-cost volatility by 62–78% — comes from 10,000
Monte Carlo paths over *assumed* mean-reverting jump-diffusion dynamics, not from
observed prices. It is prior art on **contract design**, and it is not evidence
about what tokens cost or how they move.

Anything in this repository about token price behaviour comes from
[`FINDINGS.md`](FINDINGS.md) and the tape, not from here.

---

## Bandi & Su (2026), *(Early) AI Compute Asset Pricing*

arXiv:[2607.12156](https://arxiv.org/abs/2607.12156) · August 2026

Relevant by structure rather than by subject. It treats **compute** rather than
tokens, but it establishes the result both benchmarks live with: a non-storable
good has no cash-and-carry relation linking spot to forward, so a forward is an
expectation plus a risk premium and must be *observed* rather than bootstrapped.

If a token forward curve is ever attempted here, that paper — and the failure
recorded in the sibling repository's `forward.py` — is where to start. Its
constructive path is to differentiate an observed **term** structure, which for
tokens would mean committed-throughput contracts rather than per-request rates.
No such term structure is published today, which is why nothing of the kind is
attempted.

Note that its data is the Silicon Data and Ornn compute indices, so it is a
useful reference and not a neutral one.

---

## What is deliberately absent

Nothing here is cited for the estimator, the keep band, the serving factors or
the gates. Those are defended by measurement against this tape, not by appeal to
a paper.
