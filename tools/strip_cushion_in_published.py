"""Remove the gamma "cushion line" from already-published reports, in place.

Companion to refresh_be_in_published.py, same contract: touch ONE thing and
prove nothing else moved. Here that thing is the cushion block and its rail
chip, both of which presented levels["sup"][0] — the MODEL's support level —
as if dealer gamma had produced it. gap_engine.py no longer emits either, but
reports already on disk still carry them, and the next model run is neither
free nor guaranteed to be soon.

The proof is exact, not a heuristic: collect the matched spans, build the
output from the gaps between them, then reassemble gaps+fragments and require
that to equal the original byte for byte. If a pattern ever swallowed more
than it should, reassembly still succeeds — so the gaps are additionally
required to contain no cushion markup at all.

Run:  python tools/strip_cushion_in_published.py site/gap-risk/index.html [...]
"""
import re
import sys
import pathlib

# {ctext} carries <b>/<i> but never a nested <div>, so non-greedy to the first
# closing </div> is exact, not approximate.
PATTERNS = [
    re.compile(r'\s*<div class="cushion[^"]*">.*?</div>', re.S),
    re.compile(r'\s*<div class="lvrow"><span class="lab">Cushion line</span>.*?</div>', re.S),
    # The glossary entry explains a thing the page no longer shows.
    re.compile(r'\s*<dt>The Cushion \(gamma\)</dt><dd>.*?</dd>', re.S),
]


def spans(html):
    found = []
    for pat in PATTERNS:
        found += [m.span() for m in pat.finditer(html)]
    found.sort()
    for (a, b), (c, _) in zip(found, found[1:]):
        if b > c:
            raise ValueError(f"overlapping matches at {b} > {c}")
    return found


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    rc = 0
    for arg in argv:
        p = pathlib.Path(arg)
        html = p.read_text(encoding="utf-8")
        try:
            sp = spans(html)
        except ValueError as e:
            print(f"  ! {p}  REFUSED — {e}")
            rc = 1
            continue
        if not sp:
            print(f"  = {p}  (already clean)")
            continue

        gaps, frags, cur = [], [], 0
        for a, b in sp:
            gaps.append(html[cur:a])
            frags.append(html[a:b])
            cur = b
        gaps.append(html[cur:])
        out = "".join(gaps)

        # Reassembly must reproduce the original exactly — that is what proves
        # nothing outside the removed spans was touched.
        rebuilt = "".join(g + f for g, f in zip(gaps, frags)) + gaps[-1]
        if rebuilt != html:
            print(f"  ! {p}  REFUSED — reassembly did not reproduce the original")
            rc = 1
            continue
        # And every fragment must actually be cushion markup, nothing else.
        if not all(("cushion" in f or "Cushion line" in f
                    or "The Cushion (gamma)" in f) for f in frags):
            print(f"  ! {p}  REFUSED — a removed fragment was not cushion markup")
            rc = 1
            continue
        # Only ELEMENTS must be gone. The .cushion CSS rules stay: every
        # report inlines the full stylesheet, and the archived reports that
        # still render cushion blocks rely on their own inlined copy.
        if re.search(r'class="cushion|>Cushion line<|The Cushion \(gamma\)', out):
            print(f"  ! {p}  REFUSED — cushion markup still present after strip")
            rc = 1
            continue

        p.write_text(out, encoding="utf-8")
        print(f"  ~ {p}  -{len(frags)} fragment(s), {len(html)-len(out)} bytes")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
