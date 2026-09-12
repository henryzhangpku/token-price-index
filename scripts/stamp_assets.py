"""Stamp asset URLs with a content hash, or check that they are already stamped.

A hand-written version string is a cache-busting scheme that works until
somebody forgets, and the failure is silent and expensive: the page keeps
serving the previous script, everything looks broken in a way the source does
not explain, and the obvious next move is to debug code that is not running.

That happened here. The chart was rewritten and the stamp was not bumped, so
the site served the old chart while the repository contained the new one.

Run with ``--check`` in CI: it fails if any stamp disagrees with the file it
names, which makes the mistake impossible to ship rather than merely unlikely.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
ASSETS = ("app.js", "style.css")
PAGES = ("index.html", "method.html")


def digest(name: str) -> str:
    return hashlib.sha256((WEB / name).read_bytes()).hexdigest()[:8]


def stamped(source: str, name: str, value: str) -> str:
    pattern = rf"{re.escape(name)}\?v=[0-9a-f]+"
    if re.search(pattern, source):
        return re.sub(pattern, f"{name}?v={value}", source)
    return source.replace(name, f"{name}?v={value}")


def main(check: bool) -> int:
    digests = {name: digest(name) for name in ASSETS}
    stale: list[str] = []

    for page in PAGES:
        path = WEB / page
        source = path.read_text(encoding="utf-8")
        wanted = source
        for name, value in digests.items():
            wanted = stamped(wanted, name, value)
        if wanted == source:
            continue
        if check:
            stale.append(page)
        else:
            path.write_text(wanted, encoding="utf-8")
            print(f"stamped {page}")

    if check and stale:
        print("asset stamps are stale in: " + ", ".join(stale))
        print("run: uv run python scripts/stamp_assets.py")
        print("the site would serve a cached copy of a file this repo has changed")
        return 1
    if check:
        print("asset stamps match their files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv))
