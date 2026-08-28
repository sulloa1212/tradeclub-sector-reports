"""Render the calculator's HTML+JS from a published report's own parameters.

No API call, no network: it reuses the BLOB already baked into a shipped
report, so it is safe to run any time. Writes /tmp/be.js and /tmp/be_section.html
for the checks described in tools/check_be_regression.mjs.
"""
import json, re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import gap_engine as G

src = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'site/gap-risk/index.html')
html = src.read_text(encoding='utf-8')
blob = json.loads(re.search(r'var BLOB=(\{.*?\}), BE=BLOB\.ix', html, re.S).group(1))
IX = {k: {"nm": d["nm"], "co": d["co"], "lvl": d["C"], "vol": d["vol"], "r": d["r"],
          "sig": d["sg"], "vn": d["vn"], "vol1d": d["v1"],
          "vx_spot": d["vx"], "vx1d_spot": d["vx1"],
          "on": d["on"], "wk": d["wk"],
          "on_sig": d["on"]["sd1"], "wk_sig": d["wk"]["sd1"]}
      for k, d in blob['ix'].items()}
section, script = G._be_calc(IX, G.run_context())
pathlib.Path('/tmp/be.js').write_text(re.sub(r'^\s*<script>|</script>\s*$', '', script.strip()))
pathlib.Path('/tmp/be_section.html').write_text(section)
print(f"rendered from {src} -> /tmp/be.js, /tmp/be_section.html")
