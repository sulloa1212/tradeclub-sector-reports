"""Re-render the calculator inside already-published reports, in place.

The report BODY is never touched — only the <section id="becalc"> block and its
<script>. Use it to roll a calculator change out to what is already live without
waiting for, or paying for, a fresh model run.

Verifies byte-identity outside the calculator before writing; refuses the file
if anything else moved.

Run:  python tools/refresh_be_in_published.py site/gap-risk/index.html [...]
"""
import json, re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import gap_engine as G

SEC = re.compile(r'<section id="becalc">.*?</section>', re.S)
SCR = re.compile(r'<script>\s*\(function\(\)\{\s*var BLOB=.*?</script>', re.S)

def strip(t):
    return SCR.sub('@@JS@@', SEC.sub('@@CALC@@', t))

for arg in sys.argv[1:]:
    p = pathlib.Path(arg)
    html = p.read_text(encoding='utf-8')
    blob = json.loads(re.search(r'var BLOB=(\{.*?\}), BE=BLOB\.ix', html, re.S).group(1))
    IX = {k: {"nm": d["nm"], "co": d["co"], "lvl": d["C"], "vol": d["vol"], "r": d["r"],
              "sig": d["sg"], "vn": d["vn"], "vol1d": d["v1"],
              "vx_spot": d["vx"], "vx1d_spot": d["vx1"],
              "on": d["on"], "wk": d["wk"],
              "on_sig": d["on"]["sd1"], "wk_sig": d["wk"]["sd1"]}
          for k, d in blob['ix'].items()}
    # The calculator's staleness copy quotes the ORIGINAL run time, so rebuild
    # the context from the report itself rather than from now.
    ctx = G.run_context()
    ctx["genlbl"] = blob['cfg']['genlbl']
    section, script = G._be_calc(IX, {**ctx, "gen_iso": blob['cfg']['gen'],
                                      "time_str": blob['cfg']['genlbl'],
                                      "gen_date": blob['cfg']['gday']})
    # _be_calc returns both blocks with a leading newline, because the page
    # template supplies its own surrounding whitespace. Substituting them raw
    # here would add a blank line each — harmless, but it trips the guard below,
    # and a guard you relax the first time it fires is not a guard.
    out = SCR.sub(lambda _: script.strip(), SEC.sub(lambda _: section.strip(), html))
    if strip(html) != strip(out):
        print(f"  REFUSED {p} — something outside the calculator changed"); continue
    p.write_text(out, encoding='utf-8')
    print(f"  ok {p}  ({len(html):,} -> {len(out):,} bytes, body identical)")
