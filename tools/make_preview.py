"""Build a full, clickable preview of the report with the CURRENT _be_calc.

Takes the last published report and swaps in the calculator section and script
that gap_engine generates right now. Everything else — the board, the leans,
the narrative — is the published article untouched, so what you are looking at
is exactly what tomorrow's report would render, without spending an API call.

Run:  python tools/make_preview.py [source.html]   ->  preview/gap-risk-preview.html
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
out = re.sub(r'<section id="becalc">.*?</section>', lambda _: section, html, flags=re.S)
out = re.sub(r'<script>\s*\(function\(\)\{\s*var BLOB=.*?</script>', lambda _: script, out, flags=re.S)

banner = ('<div style="position:fixed;top:0;left:0;right:0;z-index:99999;background:#f59e0b;'
          'color:#1a1200;font:800 13px/1 -apple-system,BlinkMacSystemFont,sans-serif;'
          'padding:9px 14px;text-align:center">PREVIEW &mdash; branch feat/expiration-horizon. '
          'Report body is the published article; only the calculator is new.</div>'
          '<div style="height:33px"></div>')
out = re.sub(r'(<body[^>]*>)', lambda m: m.group(1) + banner, out, count=1)

dst = pathlib.Path('preview/gap-risk-preview.html')
dst.parent.mkdir(exist_ok=True)
dst.write_text(out, encoding='utf-8')
print(f"preview -> {dst}  ({dst.stat().st_size:,} bytes, from {src})")
