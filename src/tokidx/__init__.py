"""tokidx - a reference implementation of a daily token price benchmark.

The package is organised around the lifecycle of a benchmark value:

    collect -> normalize -> screen -> estimate -> gate -> publish -> revise

Each stage is a separate module so that the methodology can be audited
independently of the plumbing that feeds it. The one decision made before
any of them run -- whether the good has more than one seller at all -- lives
in ``spec`` with the rest of the constants a dispute would be about.
"""

from .spec import METHODOLOGY_VERSION

__version__ = "0.1.0"

__all__ = ["METHODOLOGY_VERSION", "__version__"]
