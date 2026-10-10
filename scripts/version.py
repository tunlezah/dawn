#!/usr/bin/env python3
"""Dawn's version: one number, written in several files. Every PR raises it (semver) and adds a CHANGELOG section.

  python scripts/version.py                      print the version
  python scripts/version.py bump 0.3.0           write 0.3.0 everywhere
  python scripts/version.py check                all files agree
  python scripts/version.py check --newer-than origin/main   ...and the version is higher than on that ref (CI, PRs)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = r"\d+\.\d+\.\d+"

# file, pattern whose group 1 is the version (every match is the project's own version)
FILES: list[tuple[str, str]] = [
    ("core/dawn_core/__init__.py", rf'^__version__ = "({SEMVER})"'),
    ("core/pyproject.toml", rf'^version = "({SEMVER})"'),
    ("dawn-timed/dawn_timed/__init__.py", rf'^__version__ = "({SEMVER})"'),
    ("dawn-timed/pyproject.toml", rf'^version = "({SEMVER})"'),
    ("sim/pyproject.toml", rf'^version = "({SEMVER})"'),
    ("web/package.json", rf'^  "version": "({SEMVER})"'),
    ("web/package-lock.json", ""),  # twice: top level and the root package ("packages" → ""); see LOCK_*
]
LOCK_ROOT = re.compile(rf'(\n  "packages": \{{\n    "": \{{\n      "name": "[^"]+",\n      "version": ")({SEMVER})(")')
LOCK_TOP = re.compile(rf'^(  "version": ")({SEMVER})(")', re.M)


def found(path: str, text: str) -> list[str]:
    if path == "web/package-lock.json":
        return [m.group(2) for m in (LOCK_TOP.search(text), LOCK_ROOT.search(text)) if m]
    pattern = dict(FILES)[path]
    return [m.group(1) for m in re.finditer(pattern, text, re.M)]


def versions(read=lambda p: (ROOT / p).read_text()) -> dict[str, list[str]]:
    return {p: found(p, read(p)) for p, _ in FILES}


def current() -> str:
    return versions()["core/dawn_core/__init__.py"][0]


def key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def bump(new: str) -> None:
    if not re.fullmatch(SEMVER, new):
        sys.exit(f"not a version: {new}")
    for path, pattern in FILES:
        f = ROOT / path
        text = f.read_text()
        if path == "web/package-lock.json":
            text = LOCK_TOP.sub(rf"\g<1>{new}\g<3>", text, count=1)
            text = LOCK_ROOT.sub(rf"\g<1>{new}\g<3>", text, count=1)
        else:
            text = re.sub(pattern, lambda m: m.group(0).replace(m.group(1), new), text, flags=re.M)
        f.write_text(text)
    print(new)


def check(newer_than: str | None) -> int:
    vs = versions()
    flat = {v for lst in vs.values() for v in lst}
    missing = [p for p, lst in vs.items() if not lst or (p == "web/package-lock.json" and len(lst) != 2)]
    if missing or len(flat) != 1:
        for p, lst in vs.items():
            print(f"  {p}: {', '.join(lst) or 'no version found'}")
        print("version files disagree; set them with: python scripts/version.py bump X.Y.Z")
        return 1
    v = flat.pop()
    if newer_than:
        try:
            base_text = subprocess.run(["git", "-C", str(ROOT), "show", f"{newer_than}:core/dawn_core/__init__.py"], check=True, capture_output=True, text=True).stdout
        except subprocess.CalledProcessError as e:
            print(f"cannot read the version on {newer_than}: {e.stderr.strip()}")
            return 1
        base = found("core/dawn_core/__init__.py", base_text)[0]
        if key(v) <= key(base):
            print(f"version {v} is not higher than {base} on {newer_than}: every PR bumps it (python scripts/version.py bump X.Y.Z) "
                  "and adds a CHANGELOG.md section")
            return 1
        print(f"version {v} (was {base} on {newer_than})")
    else:
        print(f"version {v}")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(current())
        return 0
    if argv[0] == "bump" and len(argv) == 2:
        bump(argv[1])
        return 0
    if argv[0] == "check":
        ref = argv[2] if len(argv) == 3 and argv[1] == "--newer-than" else None
        return check(ref)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
