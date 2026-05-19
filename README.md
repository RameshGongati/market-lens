# 📈 Market Lens

A local stock market analysis application built with Python and Streamlit. Market Lens lets you build custom watchlists and run demand/supply zone detection, long-term, short-term, and intraday trading analysis — all powered by real-time data from multiple configurable sources.

---

## Features

- **Custom Watchlists** — Create up to 10 watchlists, each holding up to 10 stocks (NSE / BSE)
- **Multiple Data Sources** — Yahoo Finance (default, no auth), NSE India scraping, Zerodha Kite Connect, Upstox API, TradingView
- **Four Analysis Modes**
  - **Demand/Supply Zones** — Pivot-based zone detection; bullish near demand, bearish near supply
  - **Long Term Investment** — 200-day SMA, 52-week range positioning
  - **Short Term Investment** — 50-day SMA, RSI, MACD signals
  - **Intraday Trading** — VWAP, RSI, volume ratio analysis
- **Colour-coded Stock Cards** — Green (bullish) / Red (bearish) / Yellow (neutral) at a glance
- **Interactive Charts** — Plotly candlestick charts with zone, SMA, or VWAP overlays
- **In-app Alerts** — Toggle on/off; alerts saved to local SQLite database with toast notifications
- **Encrypted Credential Storage** — API keys encrypted with Fernet and stored at `~/.market-lens/`
- **Light Theme UI** — Clean Streamlit interface with wide layout

---

## Tech Stack

| Layer | Library |
|---|---|
| UI | Streamlit |
| Charts | Plotly |
| Data — default | yfinance |
| Data — NSE | requests + BeautifulSoup4 |
| Data — Zerodha | kiteconnect |
| Data — Upstox | upstox-python-sdk |
| Data — TradingView | tvdatafeed |
| Data processing | pandas, numpy |
| Encryption | cryptography (Fernet) |
| Database | SQLite (stdlib) |
| Logging | Python stdlib logging |

---

## Installation

### Prerequisites

- Python 3.11+
- pip

### Steps

```bash
# 1. Clone the repository
git clone <repo-url>
cd market-lens

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
.venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Copy the example environment file
cp .env.example .env
```

---

## How to Run

```bash
streamlit run app.py
```

The app opens in your browser at `http://localhost:8501`.

On first run, Market Lens will:
- Create `~/.market-lens/` directory
- Initialise the SQLite database at `~/.market-lens/market_lens.db`
- Generate an encryption key at `~/.market-lens/.key`

---

## Folder Structure

```
market-lens/
├── app.py                     # Main Streamlit entry point
├── requirements.txt
├── README.md
├── .env.example
├── .gitignore
├── config/
│   ├── settings.py            # Global constants
│   └── credentials.py         # Encrypted credential store
├── data/
│   ├── sources/
│   │   ├── base.py            # Abstract DataSource class
│   │   ├── yahoo_finance.py   # yfinance integration
│   │   ├── nse_india.py       # NSE website scraper
│   │   ├── zerodha.py         # Kite Connect scaffold
│   │   ├── upstox.py          # Upstox API scaffold
│   │   └── tradingview.py     # tvdatafeed scaffold
│   └── manager.py             # Source switcher
├── analysis/
│   ├── base.py                # Abstract BaseAnalysis class
│   ├── demand_supply.py       # Pivot-based zone detection
│   ├── long_term.py           # 200 SMA + 52-week analysis
│   ├── short_term.py          # RSI + MACD + SMA50
│   └── intraday.py            # VWAP + RSI + volume
├── watchlist/
│   ├── models.py              # Watchlist & Stock dataclasses
│   └── manager.py             # CRUD with limits enforced
├── ui/
│   ├── components/            # Reusable Streamlit components
│   └── pages/                 # Full page renderers
├── alerts/
│   ├── manager.py             # Alert trigger logic
│   └── inapp.py               # Notification handler
├── storage/
│   └── database.py            # SQLite CRUD operations
└── utils/
    ├── logger.py              # File + console logging
    └── helpers.py             # Shared utility functions
```

---

## Data Sources Explained

| Source | Auth Required | Notes |
|---|---|---|
| **Yahoo Finance** | No | Default source; use `.NS` suffix for NSE, `.BO` for BSE |
| **NSE India** | No | Scrapes NSE website; may break on site changes |
| **Zerodha Kite Connect** | api_key, api_secret, access_token | Requires Kite Connect developer account |
| **Upstox API** | api_key, api_secret, access_token | Requires Upstox developer account |
| **TradingView** | username, password | Uses tvdatafeed; requires TradingView account |

Credentials are entered via the sidebar form and stored encrypted at `~/.market-lens/credentials.json`. They are never committed to version control.

---

## Pending Features Roadmap

- Real-time auto-refresh during market hours (9:15–15:30 IST)
- Zerodha Kite Connect full integration (historical data + order placement)
- Upstox API full integration (instrument key mapping)
- TradingView full historical data support
- NSE India historical data scraping
- Portfolio P&L tracking
- Email / push alert notifications
- Custom alert conditions (price triggers, RSI thresholds)
- Multi-timeframe analysis overlay
- Export analysis report to PDF
- Chart drawing tools (trend lines, Fibonacci retracements)
- Sector-wise heatmap view

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes following the existing code style (PEP 8, type hints, docstrings)
4. Open a pull request describing what changed and why

---

## License

MIT License — see [LICENSE](LICENSE) for details.
