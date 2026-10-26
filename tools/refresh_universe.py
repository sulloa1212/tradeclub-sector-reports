"""Rebuild mwtc/data/universe.txt — the S&P 500 ∪ Nasdaq-100 list the
Pre-Market movers scan (symbol<TAB>name per line).

FMP's /sp500-constituent and /nasdaq-constituent answer 402 on the current
plan (checked 2026-10-26), so the bot reads this committed list instead. Index
membership changes a few times a quarter; re-run this when it does:

  python tools/refresh_universe.py            # writes mwtc/data/universe.txt
  python tools/refresh_universe.py --check    # print counts only

Sources (public, no key): Wikipedia's S&P 500 constituents table and Nasdaq's
own Nasdaq-100 list API.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "mwtc" / "data" / "universe.txt"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36",
      "Accept": "application/json, text/html, */*"}

_SP_ROW = re.compile(r'<tr[^>]*>\s*<td[^>]*><a[^>]*>([A-Z][A-Z.]{0,5})</a></td>\s*'
                     r'<td[^>]*>(?:<a[^>]*>)?([^<]+)')
_NDX_SUFFIX = re.compile(r"\s+(?:Class [A-Z] )?(?:Common Stock|Capital Stock|Ordinary Shares|"
                         r"American Depositary Shares?|Depositary Shares|ADS)\b.*$")


def sp500() -> list:
    page = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                        headers=UA, timeout=30).text
    table = page.split('id="constituents"', 1)[1].split("</table>", 1)[0]
    # The dotted share classes carry an editor comment after the ticker
    # ("DO NOT CHANGE THIS TICKER TO BRK-B") that would hide them from the regex.
    table = re.sub(r"<!--.*?-->", "", table, flags=re.S)
    return [(s, html.unescape(n).strip()) for s, n in _SP_ROW.findall(table)]


def nasdaq100() -> list:
    r = requests.get("https://api.nasdaq.com/api/quote/list-type/nasdaq100", headers=UA, timeout=30)
    r.raise_for_status()
    rows = r.json()["data"]["data"]["rows"]
    return [(str(x["symbol"]).strip().upper(),
             _NDX_SUFFIX.sub("", html.unescape(x.get("companyName") or "")).strip()) for x in rows]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="print counts, write nothing")
    a = ap.parse_args(argv)
    sp, nd = sp500(), nasdaq100()
    # Sanity floors: a page-layout change must fail loudly, not shrink the scan.
    if len(sp) < 490 or len(nd) < 98:
        print(f"refusing: S&P 500 parsed {len(sp)}, Nasdaq-100 {len(nd)} — source layout changed?")
        return 1
    seen, rows = set(), []
    for sym, name in sp + nd:
        if sym not in seen:
            seen.add(sym)
            rows.append(f"{sym}\t{name}")
    print(f"S&P 500 {len(sp)} + Nasdaq-100 {len(nd)} -> {len(rows)} unique symbols")
    if not a.check:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
