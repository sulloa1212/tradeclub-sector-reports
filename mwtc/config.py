"""Central configuration. Reads secrets from environment (GitHub Secrets in CI,
or a local .env file when testing locally)."""
import os
from pathlib import Path

# Load a local .env if present (no-op in CI, where vars come from Secrets).
def _load_dotenv():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

_load_dotenv()

# --- Secrets ---
# Required
UW_API_KEY = os.environ.get("UW_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
# Free
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
NASDAQ_DATA_LINK_API_KEY = os.environ.get("NASDAQ_DATA_LINK_API_KEY", "").strip()
# Paid
FMP_API_KEY = os.environ.get("FMP_API_KEY", "").strip()
# Optional (CME FedWatch OAuth — entitlement required)
CME_FEDWATCH_API_ID = os.environ.get("CME_FEDWATCH_API_ID", "").strip()
CME_FEDWATCH_API_SECRET = os.environ.get("CME_FEDWATCH_API_SECRET", "").strip()
# Optional (Kalshi). NOTE: reading Fed-decision odds needs NO key — Kalshi's
# market-data endpoints are public. These are only for future authenticated /
# portfolio calls; the read-only fed_odds() fetcher never uses them.
# KALSHI_PRIVATE_KEY holds the multiline RSA PEM verbatim (GitHub passes multiline
# secrets through unchanged, so no base64 wrapping is needed).
KALSHI_API_KEY_ID = os.environ.get("KALSHI_API_KEY_ID", "").strip()
KALSHI_PRIVATE_KEY = os.environ.get("KALSHI_PRIVATE_KEY", "").strip()

# --- Settings ---
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip()
REPORT_VERSION = os.environ.get("REPORT_VERSION", "institutional").strip().lower()
# premarket (8:40 AM ET, day ahead) | postmarket (4:30 PM ET, closing recap)
REPORT_MODE = os.environ.get("REPORT_MODE", "premarket").strip().lower()

# --- Paths ---
# PKG is the mwtc/ package dir (this file lives at mwtc/config.py). The package
# was vendored from a standalone bot whose code sat under src/; here it IS mwtc/,
# so assets are mwtc/report/assets and scratch packets go under mwtc/reports/.
PKG = Path(__file__).resolve().parent
ROOT = PKG
REPORTS_DIR = PKG / "reports"
ASSETS_DIR = PKG / "report" / "assets"

# --- Ticker universe (yfinance symbols) ---
# Index futures (overnight).
FUTURES = {
    "S&P 500 Futures": "ES=F",
    "Nasdaq 100 Futures": "NQ=F",
    "Dow Futures": "YM=F",
    "Russell 2000 Futures": "RTY=F",
}
# Rates & volatility (10Y quoted x10: 42.5 = 4.25%).
RATES_VOL = {
    "10-Year Treasury Yield": "^TNX",
    "VIX": "^VIX",
}
# Commodities.
COMMODITIES = {
    "WTI Crude": "CL=F",
    "Gold": "GC=F",
    "Silver": "SI=F",
    "Copper": "HG=F",
    "Natural Gas": "NG=F",
}
# Currencies (DXY headline + the major crosses).
FX = {
    "US Dollar Index (DXY)": "DX-Y.NYB",
    "EUR/USD": "EURUSD=X",
    "USD/JPY": "JPY=X",
    "GBP/USD": "GBPUSD=X",
    "USD/CNY": "CNY=X",
}
# Crypto.
CRYPTO = {
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
}
# Cash indices (for the technical picture).
INDICES = {
    "S&P 500": "^GSPC",
    "Nasdaq Composite": "^IXIC",
    "Dow Jones": "^DJI",
    "Russell 2000": "^RUT",
}
GLOBAL_INDICES = {
    "Nikkei 225": "^N225",
    "Hang Seng": "^HSI",
    "Shanghai": "000001.SS",
    "DAX": "^GDAXI",
    "FTSE 100": "^FTSE",
    "CAC 40": "^FCHI",
}
SECTOR_ETFS = {
    "Technology": "XLK", "Financials": "XLF", "Energy": "XLE",
    "Health Care": "XLV", "Industrials": "XLI", "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP", "Utilities": "XLU", "Materials": "XLB",
    "Real Estate": "XLRE", "Communication Services": "XLC",
}
# Institutional-data tickers (pulled from Unusual Whales) + their tradable ETF
# proxy used to get a spot price for the implied-move calc.
UW_FOCUS_TICKERS = ["SPY", "QQQ", "DIA", "IWM"]  # SPX, NDX, DJX, RUT proxies

# Universe scanned to BUILD the IV-rank lists (elevated vs low). UW exposes IV
# percentile per-ticker (/interpolated-iv) but has NO market-wide IV screener in
# its endpoint whitelist, so the lists are derived by scanning this curated set
# of liquid, optionable names and ranking by IV percentile. One call each — keep
# it modest. Labeled as a scanned universe in the report (not "the whole market").
OPTIONS_IV_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "AVGO", "NFLX",
    "MU", "SMCI", "INTC", "QCOM", "CRM", "ADBE", "ORCL", "PLTR", "COIN", "MSTR",
    "JPM", "BAC", "GS", "XOM", "CVX", "UNH", "LLY", "WMT", "COST", "HD",
    "DIS", "BA", "CAT", "SPY", "QQQ", "IWM", "SMH", "XLE", "GLD", "TLT",
]
# Names we pull per-contract top-strike (highest volume & OI) detail for.
OPTIONS_TOP_STRIKE_TICKERS = ["AAPL", "NVDA", "TSLA", "MU", "AMD", "META", "SPY", "QQQ"]

# Keywords used to surface Fed-relevant headlines from the news feed.
FED_KEYWORDS = ["fed", "fomc", "powell", "warsh", "rate", "inflation", "pce",
                "cpi", "treasury yield", "dot plot", "hawkish", "dovish"]
