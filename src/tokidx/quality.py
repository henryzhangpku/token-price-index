"""Checks for failures that arrive as well-formed data.

A source returning HTTP 500 is caught by the collector. A source returning
last month's prices forever, or quietly dropping its largest seller, or one
seller repricing by a factor of four overnight, all arrive as valid numbers
and would otherwise flow straight into a published value.

Every check here needs history, which is why they live next to the store
rather than next to the estimator. And they are honest about not having it:
with a single collection date on file, a check that cannot be evaluated
returns ``not_evaluable`` rather than ``pass``. A control reporting success
because it had nothing to compare against is worse than no control, because
it is indistinguishable from one that looked.

One difference from the compute benchmark worth stating plainly. There, the
contributor-shift threshold is 25%, measured from 403 observations of daily
provider moves where p99 was 21.2%. Here there is no such history yet, so the
threshold below is **judgement, not calibration**, and is marked as such. It
will be re-derived from observed moves once the series is long enough to
support one, and until then it is a placeholder with a number attached rather
than a measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .models import Flag

#: Days after which the collected prices are too old to publish against.
MAX_STALENESS_DAYS = 7

#: Fraction by which the fixing may move between consecutive dates before it
#: is flagged for a human. Judgement, not calibration -- see module docstring.
INDEX_LEVEL_SHIFT = 0.20

#: Fraction by which one seller may move between consecutive dates before it
#: is flagged. Judgement, not calibration.
#:
#: The compute benchmark's equivalent was set at 25% from 403 observations
#: with p99 at 21.2%. Token list prices are expected to move in steps rather
#: than continuously -- a seller reprices a model, or does not -- so the
#: distribution is likely more bimodal than the compute one, not less. That
#: argues for a *higher* threshold once measured, not a lower one. Held at 25%
#: for now purely so the two repositories are comparable.
PROVIDER_LEVEL_SHIFT = 0.25

NOT_EVALUABLE = "not_evaluable"


@dataclass
class CheckResult:
    """A check's answer, including the answer 'I could not look'."""

    code: str
    #: ``pass``, ``fail`` or ``not_evaluable``.
    status: str
    detail: str

    @property
    def evaluable(self) -> bool:
        return self.status != NOT_EVALUABLE

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def as_flag(self) -> Flag | None:
        """A flag for the fixing, or None where there is nothing to report."""
        if self.status == "fail":
            return Flag(severity="warn", code=self.code, detail=self.detail)
        if self.status == NOT_EVALUABLE:
            return Flag(severity="info", code=self.code, detail=self.detail)
        return None


def check_staleness(collected_at: datetime, index_date: date) -> CheckResult:
    """Are the prices recent enough to describe the day being priced?"""
    age = (index_date - collected_at.date()).days
    if age < 0:
        return CheckResult(
            "staleness", "fail",
            f"prices collected {abs(age)} day(s) after the index date, which is "
            "the future relative to the fixing",
        )
    if age > MAX_STALENESS_DAYS:
        return CheckResult(
            "staleness", "fail",
            f"prices are {age} days old against a ceiling of {MAX_STALENESS_DAYS}",
        )
    return CheckResult("staleness", "pass", f"prices are {age} day(s) old")


def check_provider_dropout(
    previous: dict[str, float], current: dict[str, float]
) -> CheckResult:
    """Did a seller that was contributing yesterday vanish today?

    A quiet dropout is the dangerous shape. The remaining sellers still agree,
    dispersion still passes, the gates still hold -- and the fixing is now
    made of fewer, differently-selected participants than the one before it.
    """
    if not previous:
        return CheckResult("provider_dropout", NOT_EVALUABLE,
                           "no previous fixing on file to compare against")
    missing = sorted(set(previous) - set(current))
    if missing:
        return CheckResult(
            "provider_dropout", "fail",
            f"{', '.join(missing)} contributed to the previous fixing and is absent",
        )
    return CheckResult("provider_dropout", "pass",
                       f"all {len(previous)} previous seller(s) still contributing")


def check_index_level_shift(
    previous_value: float | None, current_value: float | None
) -> CheckResult:
    """Did the published value move more than a normal day's worth?"""
    if previous_value is None or current_value is None:
        return CheckResult("index_level_shift", NOT_EVALUABLE,
                           "needs two consecutive published values")
    if previous_value <= 0:
        return CheckResult("index_level_shift", NOT_EVALUABLE,
                           "previous value is not positive")
    move = current_value / previous_value - 1.0
    if abs(move) > INDEX_LEVEL_SHIFT:
        return CheckResult(
            "index_level_shift", "fail",
            f"{move:+.1%} against a {INDEX_LEVEL_SHIFT:.0%} threshold",
        )
    return CheckResult("index_level_shift", "pass",
                       f"{move:+.1%}, inside {INDEX_LEVEL_SHIFT:.0%}")


def check_provider_level_shift(
    previous: dict[str, float], current: dict[str, float]
) -> CheckResult:
    """Did one seller reprice far enough to move the fixing on its own?

    This is the defence against a contributor with a position. The outlier
    screen does not catch it: a seller moving from $4.00 to $0.90 in a
    three-seller market drags the median with it rather than standing away
    from it, so it never reaches three sigma. Only a comparison against that
    seller's own previous price sees it.
    """
    if not previous:
        return CheckResult("provider_level_shift", NOT_EVALUABLE,
                           "no previous fixing on file to compare against")

    moves: list[tuple[str, float]] = []
    for provider, price in sorted(current.items()):
        before = previous.get(provider)
        if before is None or before <= 0:
            continue
        moves.append((provider, price / before - 1.0))

    if not moves:
        return CheckResult("provider_level_shift", NOT_EVALUABLE,
                           "no seller appears in both fixings")

    breaches = [(p, m) for p, m in moves if abs(m) > PROVIDER_LEVEL_SHIFT]
    if breaches:
        detail = "; ".join(f"{p} {m:+.1%}" for p, m in breaches)
        return CheckResult(
            "provider_level_shift", "fail",
            f"{detail} against a {PROVIDER_LEVEL_SHIFT:.0%} threshold",
        )
    worst = max(moves, key=lambda pair: abs(pair[1]))
    return CheckResult(
        "provider_level_shift", "pass",
        f"largest move {worst[0]} {worst[1]:+.1%}, inside "
        f"{PROVIDER_LEVEL_SHIFT:.0%}",
    )


def run_checks(
    *,
    collected_at: datetime,
    index_date: date,
    previous_prices: dict[str, float],
    current_prices: dict[str, float],
    previous_value: float | None,
    current_value: float | None,
) -> list[CheckResult]:
    """Every check, in the order a reader would want them."""
    return [
        check_staleness(collected_at, index_date),
        check_provider_dropout(previous_prices, current_prices),
        check_provider_level_shift(previous_prices, current_prices),
        check_index_level_shift(previous_value, current_value),
    ]


def since(index_date: date, days: int = 1) -> date:
    """The date a check should compare against."""
    return index_date - timedelta(days=days)
