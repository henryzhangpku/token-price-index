# Methodology

Version 0.1.0. Every constant this document argues about lives in
[`src/tokidx/spec.py`](src/tokidx/spec.py), so a disputing reader can argue
with a number rather than with a codebase.

## 1. What is being measured

The price of one million tokens of LLM inference, in USD, for a named model,
in one direction (input or output), served synchronously without caching or
batching, at a stated context length, in the US.

Input and output are separate indices and are never blended. Every seller
prices them differently, workloads consume them in different ratios, and any
blended figure would depend on an assumed ratio that is not observable.

## 2. Which goods can carry an index, and which cannot

This is the first decision and the one that shapes everything else.

**A benchmark requires a good with more than one seller.** Price discovery is
what an index measures; where there is nothing to discover, an index reports a
private decision under a public name.

**Proprietary frontier models fail this test outright.** Opus 5 has one seller.
GPT-6 Astra has one seller. There is no second price for the same good. An
average across *different* frontier models is worse still — it silently
assumes those models are substitutes, which is precisely the assumption a
buyer is paying to avoid making.

`TIX-FRONTIER-OUT` exists in this repository to be refused. Its contract is
deliberately ill-defined (`model = "frontier"`), because that fiction is the
point: the moment you try to write the contract down, you discover there is no
single good to write.

**Open-weight models pass.** Identical weights are served by many independent
providers, competing on price, throughput and latency. On the 8 September
observation set, Kimi K2.6 output sells at $3.50, $4.00 and $4.50 per million
tokens across three sellers — a 29% spread on the same artefact. That is a
market.

The `indexable_good` gate encodes this, and it is evaluated first.

## 3. Coverage

| index | benchmark good | status |
|---|---|---|
| `TIX-GLM53-OUT` | GLM 5.3 Flash, output | published — 27 sellers |
| `TIX-GLM53-IN` | GLM 5.3 Flash, input | published — 27 sellers |
| `TIX-K3-OUT` | Kimi K3, output | published — 16 sellers after screening |
| `TIX-DSV41-OUT` | DeepSeek V4.1 Flash, output | published — 12 sellers |
| `TIX-MM3-OUT` | MiniMax M3, output | **withheld — dispersion**, 0.920 against a 0.60 ceiling |
| `TIX-FRONTIER-OUT` | GPT-6 Astra, output | **withheld — not an indexable good** |

The last row is refused for a reason no amount of data would change. The
weights are not published, so whatever it costs is one company's list price;
the two sellers on the tape resell that company's model rather than competing
to serve the same weights. Coverage here is a statement about which goods exist
as goods, not about how much was collected.

## 4. The waterfall

| tier | weight | what it proves |
|---|---|---|
| 1 · list | 1.00 | published rate card, transactable today by any customer |
| 2 · aggregated | 0.60 | seen through a router; the router's margin is not visible |
| 3 · curated | 0.25 | administrator's sourced, dated entry |

**This differs from a compute-rental benchmark in a way worth stating.** There,
a published rate card proves only that somebody published a number — capacity
may not exist at that price. Here, a published token price is directly
transactable: anyone with a card can buy at it this afternoon. So tier 1 is
genuinely populated, and the evidence base is better than it is for GPU rental.

The offsetting weakness is in section 6: enterprise pricing is negotiated and
invisible, so what is well-observed is the retail tail.

## 5. Restatement

Non-standard serving is restated onto the standard contract:

| serving | factor | note |
|---|---|---|
| standard | 1.00 | the benchmark good |
| batch | 2.00 | published at 50% off by every major seller |
| cached | 10.00 | cache reads run at roughly a tenth of standard input |
| priority | 0.50 | published at 2x by at least one seller |

Cumulative adjustment is capped at **5.0x**, which binds on the cache factor.
Restating a cache-read price to standard multiplies it by ten, so nine tenths
of the resulting number would come from this schedule rather than from the
seller. That is not an observation about standard pricing; it is the schedule
talking, and it is discarded. Batch at 2x survives, because half the number is
still the seller's.

An earlier draft set the ceiling at 10.5x, which no combination of factors
could reach — serving is a single enum and the context factor only reduces. A
ceiling nothing can touch is not a control. A test caught it.

**Two of these four factors are unusually well grounded**, and it is worth
being precise about why. Anthropic publishes batch at 50% and cache reads at
about a tenth of standard input. OpenAI publishes the same two ratios. **A
ratio published identically by two independent sellers is a market convention;
a ratio applied identically across every SKU of a single seller is that
seller's discount policy and carries no information.** That distinction is what
makes the batch and cache factors defensible and leaves the context factor as
judgement.

`tokidx calibrate` prints the check: where one seller publishes the same good
two ways, the observed ratio is compared against the asserted factor.

## 6. Estimation and gates

Each seller collapses to one median, so catalogue size buys no influence.
Providers more than **3.0 robust sigma** from the provider median are screened,
using MAD × 1.4826 rather than a standard deviation — MAD has a 50% breakdown
point and standard deviation has none. Where MAD is zero, because every honest
seller agrees exactly, the sigma test is undefined and the screen falls back to
a **symmetric ratio band** at 3.0x. A percentage test would be wrong twice: it
flags a seller 50% below consensus, which is ordinary here, and it caps at 100%
on the downside, so it could never catch a near-zero price against a
three-dollar market.

Weights come from the waterfall, then no single seller may carry more than
**50%** of total weight, applied iteratively.

Publication requires **all** of:

| gate | threshold |
|---|---|
| indexable good | required |
| min providers | 3 |
| min observations | 3 |
| dispersion | ≤ 0.60 |

Undefined dispersion is not a pass. Below three providers it is not computable,
and a fixing whose disagreement cannot be measured is refused rather than
assumed to be tight.

## 7. Data quality

Checks that catch failures arriving as well-formed data: a source returning last
month's prices forever, quietly dropping its largest seller, or one seller
repricing by a factor of four overnight. All of them need history.

| check | fires when |
|---|---|
| staleness | collected prices are more than 7 days older than the index date |
| seller dropout | a seller that contributed to the previous fixing is absent |
| index level shift | the published value moves more than 20% between dates |
| seller level shift | one seller moves more than 25% between dates |
| price concentration | a majority of sellers quote one identical price |

**Where there is no history, a check reports `not_evaluable` rather than
passing.** A control that reports success because it had nothing to compare
against is worse than no control, because it is indistinguishable from one that
looked. The series began on 12 September 2026, so most of the above currently
report exactly that.

**And none of these thresholds are calibrated.** The compute benchmark sets its
seller-shift threshold at 25% from 403 observed daily moves with p99 at 21.2%.
The equivalent number here is judgement with a value attached, and is marked as
such in `quality.py`. It will be re-derived once the series is long enough to
support one.

## 8. Revisions

Storage is bitemporal and append-only. Two clocks are tracked separately: when a
price was true in the market, and when this system first knew it.

A correction never edits a published value. It writes a new revision, stamps the
previous one as superseded, and **must carry a reason** — a restatement nobody
explained is indistinguishable from a bug, and makes the history unreadable by
the only people who would ever need it.

This is what makes `as_of(index_date, knowledge_time)` answerable: *what did we
say for the 12th, as known on the morning of the 13th* is a different question
from *what do we believe the 12th was*, and only the first one helps a
settlement agent resolving a dispute months later. A store that overwrites in
place can answer the second and never the first.

## 9. What this cannot do

**1. No transaction data.** Every input is a published rate card. Nobody here
observes what anyone paid. It is the largest gap between this and something a
contract could settle against, and no estimator closes it.

**2. It measures the retail tail.** Enterprise inference is bought on
negotiated contracts with committed volume, and none of that is public.
Anything settling against this carries that basis risk.

**3. No quality adjustment, and this is the deepest problem.** Price per token
falls while model capability rises. This index treats a token as a token, so a
falling series cannot distinguish "inference got cheaper" from "the models got
better and the price of the old capability collapsed." That is the **hedonic
adjustment** problem official statisticians solve for computer prices in CPI,
using regression on quality attributes. It is not solved here, and it is not a
detail — for a good improving this fast it may be the dominant term.

**4. Model identity is assumed, not verified.** Two sellers listing
"Kimi K2.6" may serve different quantisations, context limits, or throughput.
The contract pins the weights and the direction; it does not pin what you
actually get. A cheaper seller may be selling something different.

**5. Curated inputs.** Prices are read from published pages, dated and sourced,
but they are a snapshot rather than a live scrape. Sellers themselves warn
their pricing moves often, so a stale entry is a defect.

**6. Thin panels.** Two of the five contracts have only two sellers each and
withhold on provider count. The open-model serving market is real but young,
and a three-seller floor already sits below what a compute benchmark would
accept.

**7. The dispersion ceiling is a guess.** 0.60 is looser than a compute
benchmark's 0.45, on the reasoning that identical weights genuinely price
across a wide band because sellers differ on throughput, context and latency —
none of which this contract pins down. That reasoning is plausible and
untested. It is recorded as judgement rather than dressed up as calibration.

## 10. Provenance

Every observation carries a source URL and an observation date, so any
published value can be checked by hand against the seller's own page. Prices
were read on 8 September 2026.

## 11. Changing this document

Every number in `spec.py` is a methodology decision rather than an
implementation detail, and a change to one is a change to what the index means.

A change that alters what a published value would have been is a **methodology
version bump**, recorded in the fixing and carried in the bundle, so that a
reader comparing two dates can tell whether the market moved or the rules did.
Historical values are not recomputed under new rules — they stay as published,
which is the point of the store.

Two changes have been made since the first version, and both are documented
where they live rather than only here: the cumulative adjustment ceiling (an
unreachable bound, caught by a test) and the dispersion measure (a collapse on
real data, caught by reading the board). Both changed what the index would
print, and both say so.
