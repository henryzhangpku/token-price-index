# Findings

What building this actually turned up, with the numbers. The README carries the
summary; this is the working.

Every figure below comes from the collection of **12 September 2026** — 102
observations across 37 sellers, one venue. Reproduce any of it with
`uv run tokidx publish` after `uv run tokidx collect`.

---

## 1. The dispersion gate read perfect agreement on a market spanning six times

> The figures in this section were measured under a context factor that has
> since been removed (finding #7). In the raw data the modal price is $0.50,
> held by seventeen of twenty-seven sellers rather than fifteen. The shape of
> the finding — a majority at one number, so MAD is zero — is unchanged.

The gate exists to refuse publication when sellers disagree too much to be
represented by one number. On the first live collection it reported **0.000** on
`TIX-GLM53-OUT`, where the cheapest seller charged $0.163 and the dearest
$0.975.

The cause is not a bug in the arithmetic. It is the arithmetic.

| | |
|---|---|
| sellers | 27 |
| distinct prices | 11 |
| **sellers at the modal price** | **15 of 27** |
| range | $0.163 – $0.975 (**6.0×**) |
| MAD × 1.4826 / median | **0.000** |
| Qn / median | **0.000** |
| p90 of deviation / median | 0.340 |

With a majority at one number the median *is* that number, so more than half the
deviations from it are exactly zero, and the median deviation — MAD — is zero.

**Qn fails identically**, which was the surprise. Its usual advantage is that it
does not depend on a centre at all; it is a quantile of pairwise differences. But
with fifteen sellers at one price, more than a quarter of all pairs differ by
exactly zero, so its 0.25 quantile is zero too.

The general statement is worth keeping: **any statistic asking what a typical
deviation from the middle looks like answers zero when the middle is most of the
mass.** That is a property of the market, not of the estimator.

### And it was not merely blind, it was inverted

A market with fifteen sellers anchored and twelve scattered over a 6× range
scored *better* — 0.000 — than one where all twenty-seven sat within five
percent. The gate ranked the worse market as the tighter one.

### The fix, and what it costs

Dispersion is now the **ninetieth percentile of absolute deviation** over the
median. It has to reach past the mode to find a value, so it measures the
disagreement that is there.

That trades breakdown point: 10% against MAD's 50%. The trade is acceptable
because **the screen has already removed contaminants before dispersion is
computed** — robustness belongs in the screen, measurement belongs in the
measure. MAD is still what the screen uses, and for the same reason it always
was.

The change has teeth rather than being cosmetic:

| index | before | after | |
|---|---|---|---|
| `TIX-MM3-OUT` | 0.000 → **published** | 0.920 → **withheld** | 7 of 12 sellers at one price, 3.1× range |
| `TIX-DSV41-OUT` | 0.171 | 0.495 | published either way |
| `TIX-GLM53-OUT` | 0.000 | 0.340 | published either way |

---

## 2. The same defect does not exist in the compute benchmark, and the reason is market structure

The obvious next question is whether
[gpu-price-index](https://github.com/henryzhangpku/gpu-price-index) has the same
hole. It does not, and the reason is worth more than the answer.

| index | sellers | at modal price | MAD / median |
|---|---|---|---|
| `GIX-H100` | 15 | **1** | 0.410 |
| `GIX-A100` | 9 | **1** | 0.470 |
| `GIX-B200` | 6 | **1** | 0.352 |
| `TIX-GLM53-OUT` | 27 | **15** | 0.000 |

**No two GPU providers quote an identical price.** Rental prices are set
independently — different hardware, regions, power costs, margins, utilisation —
so there is no reference price to copy and exact ties essentially never occur.

**Token prices are copied.** A seller hosting someone else's open weights adopts
the publisher's reference rate, so ties are not an edge case, they are the
dominant feature.

So the two repositories use different scale estimators, and the justification is
measured rather than stylistic: *the right estimator depends on how the market
forms prices.* Swapping MAD out of the compute benchmark would break a gate
calibrated against it to fix a problem that market does not have.

---

## 3. Counting sellers overstates independence twice over

**Through venues.** Every observation arrives through one source. Thirty-seven
sellers behind a single feed is thirty-seven sellers and one point of failure: if
it changed schema or went away, the seller count would keep reporting
thirty-seven right up until it reported none. The compute benchmark found the
same shape in its own inputs — twelve providers, five venues, one aggregator
behind eight of them, and **both count gates still passed** when that feed was
removed.

**Through prices.** Fifteen sellers quoting one number are not fifteen opinions.
They are one number and fourteen followers of it, and any statistic computed
across them will report agreement it never actually measured.

Both are now measured and printed next to the fixing. `modal_share` is the price
analogue of venue concentration, and a fixing above 50% carries a flag saying so.

---

## 4. A ceiling nothing could reach is not a control

The cumulative adjustment cap was **10.5×**. No combination of factors could
reach it: serving is a single enum, so at most one serving factor applies, and
the context factor only reduces. The highest attainable total was the cache
factor alone, at 10.0×.

A test caught it. The cap is **5.0×** now, which binds on the cache factor —
restating a cache read to standard multiplies by ten, so nine tenths of the
result would come from the schedule rather than from the seller. Batch at 2×
survives, because half the number is still the seller's.

---

## 5. A fixing belongs to its inputs, not to the clock

The index date came from `date.today()`. Re-running an unchanged observation set
on a later day produced a fixing stamped with the later day.

CI caught it: the committed site bundle stopped matching a freshly generated one
the moment the date rolled over. That looked like a build annoyance and was not.
**It is the carry-forward behaviour the gates exist to prevent, arriving through
the back door** — yesterday's prices quietly becoming today's published value
with no new observation behind it.

The index date now derives from the collection timestamp. A backfill can still
name an explicit date; only the default moved.

---

## 6. A published number needs a stated precision

Two orderings of the same market produced values differing in the last bit.
Floating-point addition is not associative and nothing rounded, so the fixing was
reproducible only up to summation order.

Found by a property test comparing two orderings — which then failed a *second*
time, on a bound written with a floating-point epsilon. Rounding to four places
can carry the published value half a unit past the extremes of the contributing
range, so the correct tolerance is one published unit, not an epsilon.

A property test that passes on a retry has not passed.

---

## 7. The context factor was restating a window as a premium

Every observation carries `context_tokens`. The collector fills it from the
source's `context_length` — the size of the window a seller's rate covers.
Of 110 observations on 14 September, 82 say 1,048,576 and none say 128,000.
Every one of them is `standard` serving at a single flat price.

The restatement step read that field as a long-context *tier* — a premium
rate above a base rate — and applied 0.80 for windows up to four times the
contract's and 0.65 beyond. There was no premium to remove. A seller quoting
one rate for a million-token window serves a 128k request at that rate; that
rate *is* the price of the benchmark good.

The effect was uniform and invisible from inside the pipeline. The sellers
were consistent, the gates held, the tests passed, and the site printed
$0.325 for GLM 5.3 output while seventeen of twenty-seven sellers charged
$0.50. Kimi K3 printed 9.32 against a market at 14.34. DeepSeek V4.1 printed
0.82 against 1.19 — and read a dispersion of 0.184 where the market's is
0.033, because sellers quoting identical prices at different window sizes
were being multiplied by different factors and spread apart.

It was found the day the sensitivity measure was ported from the compute
benchmark. Its first run reported **0 of 27 quotes conforming** and **100% of
every fixing's weight resting on the context factor**. The measure exists to
answer "how much of this number is the schedule rather than the market"; the
answer was *all of it*, and that was the finding.

Context is a fitness test now, not a factor. A window that reaches the
contract's is the benchmark good and nothing is restated. A window that falls
short cannot serve the request, which is a different good rather than a
cheaper one, and is discarded as `context_too_short`. The serving factors are
untouched; on this source they never fire either, so the published values are
what sellers publish.

Finding #1's figures were measured under the removed factor: its "fifteen of
twenty-seven at $0.325" is seventeen of twenty-seven at $0.50 in the raw
data, and the shape of that finding — a majority at one number, MAD zero —
is unchanged by the correction. If anything it is sharper: two sellers the
factor had pushed off the mode are back on it.

## Reproducing

```bash
uv run tokidx collect                  # writes data/observations/<date>.json
uv run tokidx publish                  # the board
uv run pytest -q                       # 42 tests
```

Findings 1, 2 and 3 are visible on the dashboard; findings 4, 5 and 6 are
recorded where they were fixed, in `spec.py`, `pipeline.py` and `estimator.py`
respectively.
