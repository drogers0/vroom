#!/usr/bin/env python3
"""Byte-identical output comparison between two vroom binaries.

Runs both binaries on every instance of the given directories (-t 1),
normalizes outputs by dropping computing_times, and reports mismatches.
For each mismatch, reruns both binaries several times and reports the
distinct output hashes per binary: the solver has rare run-to-run
nondeterminism on some instances, and a mismatch whose hashes overlap
across binaries (or where one binary alone produces several outputs) is
that nondeterminism, not a behavioral difference.

This is the rerunnable source for every byte-identical claim in the design
doc and commit messages (unconstrained parity, refactor equivalence).

Usage: check_byte_identical.py BIN_A BIN_B DIR [DIR...]
"""

import hashlib
import json
import pathlib
import subprocess
import sys

RERUNS = 4


def normalized(binary: str, path: pathlib.Path) -> str:
    r = subprocess.run([binary, "-i", str(path), "-t", "1"],
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    d.get("summary", {}).pop("computing_times", None)
    for route in d.get("routes", []):
        route.pop("computing_times", None)
    return json.dumps(d, sort_keys=True)


def digest(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:10]


def main() -> None:
    bin_a, bin_b = sys.argv[1], sys.argv[2]
    dirs = [pathlib.Path(d) for d in sys.argv[3:]]
    total = 0
    diffs = []
    for d in dirs:
        for f in sorted(d.glob("*.json")):
            if f.name == "dropped.json":
                continue
            total += 1
            if normalized(bin_a, f) != normalized(bin_b, f):
                diffs.append(f)
                print(f"DIFF {f}")
    print(f"checked={total} diffs={len(diffs)}")
    for f in diffs:
        print(f"flake protocol for {f}:")
        for name, b in (("A", bin_a), ("B", bin_b)):
            hashes = {}
            for _ in range(RERUNS):
                h = digest(normalized(b, f))
                hashes[h] = hashes.get(h, 0) + 1
            print(f"  {name} ({b}): " +
                  " ".join(f"{h}x{c}" for h, c in sorted(hashes.items())))
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    main()
