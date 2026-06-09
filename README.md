# 📈 Market Lens

A local stock market analysis application built with Python and Streamlit. Market Lens lets you build custom watchlists and analyse stocks with a **two-axis model** — pick a **Trading Type** (time horizon) and a **Primary Strategy** (Demand/Supply Zones or Trend Following), then layer optional **ODD Enhancers** on top — all powered by real-time data from multiple configurable sources.

---

## Features

- **Stock Search with Autocomplete** — Search from 600+ NSE/BSE stocks by symbol or company name; exchange is auto-filled on selection
- **Custom Watchlists** — Create up to 10 watchlists, each holding up to 10 stocks (NSE / BSE)
- **Multiple Data Sources** — Yahoo Finance (default, no auth), NSE India scraping, Zerodha Kite Connect, Upstox API, TradingView
- **Two-Axis Analysis Model** — choose independently along two axes from the sidebar:
  - **Trading Type** (time horizon, sets the default candle timeframe): Options Trading, Intraday Trading, Short-term Trading, Long-term Investment
  - **Primary Strategy** (the base method):
    - **Demand/Supply Zones** — institutional legin/base/legout zone detection with the 7-point ODD trade score and a 50-SMA "clock method" trend filter
    - **Trend Following (SMA50/EMA20)** — 50/200-SMA golden-cross / death-cross signals (BUY / SELL / HOLD) with trend context
  - **ODD Enhancers** (optional, multi-select): Fibonacci Confluence, EMA 20 Confluence, RSI _(RSI is selectable but not yet wired into scoring)_
- **Confidence / Strength Rating** — Strong / Medium / Weak badge on every stock card and detail view, derived from the active strategy's signal conviction
- **Candle-Interval Selector** — On the detail chart, switch the candle interval (Daily / Weekly / Monthly / 75m / 15m) independently of the trading type; changing it re-fetches data **and** re-runs the analysis at that interval so the overlays stay consistent (75m is resampled from 15m; intraday falls back to Daily when unavailable)
- **Candlestick & Line Chart Toggle** — Switch between candlestick and line chart; volume and RSI subplots included
- **Colour-coded Stock Cards** — Company name, current price, absolute + percentage change, strength badge, and last-updated timestamp
- **Market Status Indicator** — Live IST clock, green/red open/closed banner, and countdown to next open or close in the sidebar
- **Analysis History** — Every run is preserved in the local database; a timeline table on the detail view shows the last 7 results with trend direction (improving / deteriorating / stable)
- **Personal Notes per Stock** — Add, view, and delete timestamped notes on the stock detail page
- **Filter & Sort Dashboard** — Filter results by status (Bullish / Bearish / Neutral) and strength (Strong / Medium / Weak); sort by status, strength, price change %, or alphabetically
- **Export Analysis Results** — Download a three-sheet Excel workbook (Summary, Details, Alerts) or a formatted PDF report from the dashboard toolbar. Exports adapt to the active strategy (zone rows for Demand/Supply, signal/cross rows for Trend Following) and save to your **Windows Downloads** folder (`Downloads/market-lens`) when running under WSL, falling back to `~/market-lens-exports`
- **Smart Defaults & Re-run** — Sidebar selections persist across sessions via `~/.market-lens/user_preferences.json`; one-click "Re-run Last" button with timestamp
- **Interactive Charts** — Plotly charts with zone overlays, SMA/VWAP series, and RSI subplot
- **In-app Alerts** — Icon/toggle only for now; when on, alerts are saved to the local SQLite database and surfaced as in-app toast notifications (no Telegram/email yet)
- **Encrypted Credential Storage** — API keys encrypted with Fernet and stored at `~/.market-lens/`
- **Light Theme UI** — Clean Streamlit interface with wide layout

---

## Screenshots

> _Screenshots will be added after the first stable release._

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
| Data — TradingView | tvdatafeed _(pending)_ |
| Data processing | pandas, numpy |
| Encryption | cryptography (Fernet) |
| Database | SQLite (stdlib) |
| Excel export | openpyxl |
| PDF export | reportlab |
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
- Create `~/.market-lens/user_preferences.json` for saved sidebar selections

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
│   ├── trading_config.py      # Two-axis model: trading types, strategies, enhancers, timeframes
│   ├── credentials.py         # Encrypted credential store
│   └── preferences.py         # User preference persistence (+ old-schema migration)
├── data/
│   ├── stock_list.json        # 600+ NSE/BSE stocks for autocomplete
│   ├── sources/
│   │   ├── base.py            # Abstract DataSource class
│   │   ├── yahoo_finance.py   # yfinance integration
│   │   ├── nse_india.py       # NSE website scraper
│   │   ├── zerodha.py         # Kite Connect scaffold
│   │   ├── upstox.py          # Upstox API scaffold
│   │   └── tradingview.py     # tvdatafeed scaffold
│   └── manager.py             # Source switcher
├── analysis/
│   ├── base.py                # Abstract BaseAnalysis + Strength type
│   ├── demand_supply.py       # Legin/base/legout zone engine + ODD score
│   ├── trend_following.py     # 50/200 SMA golden-cross / death-cross strategy
│   ├── zone_engine/           # Zone detection, scoring, trend, EMA20 + Fibonacci enhancers
│   ├── long_term.py           # (legacy single-axis helper)
│   ├── short_term.py          # (legacy single-axis helper)
│   └── intraday.py            # (legacy single-axis helper)
├── watchlist/
│   ├── models.py              # Watchlist & Stock dataclasses
│   └── manager.py             # CRUD with limits enforced
├── ui/
│   ├── components/
│   │   ├── stock_card.py      # Colour-coded card with price & strength
│   │   ├── stock_detail.py    # Chart toggle, history, notes, export
│   │   ├── watchlist_panel.py # Watchlist management + autocomplete search
│   │   ├── sidebar.py         # Market status, smart defaults, re-run
│   │   ├── alerts_toggle.py
│   │   ├── credentials_form.py
│   │   └── notifications.py
│   └── pages/
│       ├── dashboard.py       # Filter/sort grid + export buttons
│       ├── watchlist_manager.py
│       └── settings.py        # Preferences, data management, roadmap
├── alerts/
│   ├── manager.py             # Alert trigger logic
│   └── inapp.py               # Notification handler
├── storage/
│   └── database.py            # SQLite CRUD + history + notes
└── utils/
    ├── logger.py              # File + console logging
    ├── helpers.py             # format_currency, format_timestamp, search_stocks
    ├── market_hours.py        # NSE market hours, IST clock, countdown
    └── export.py              # Excel (openpyxl) and PDF (reportlab) export
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

## Known Limitations & Roadmap

**Current limitations (honest status):**

- **RSI enhancer** is selectable in the sidebar but **not yet wired** into scoring — selecting it currently has no effect on the analysis
- **Options Trading** trading type is available as a time horizon, but a dedicated options-specific strategy/spec is still **pending** (it currently uses the chosen primary strategy on daily data)
- **Intraday data** (15m / 75m) is **limited by data providers** for Indian stocks — when unavailable the app falls back to Daily candles with a notice
- **Alerts** are **in-app only** (toggle + toast); there is no Telegram or email delivery yet
- **TradingView** full data integration is pending a stable library; the app links out to tradingview.com in the meantime

**Planned features:**

- Wire up the RSI enhancer into the confluence scoring
- Dedicated Options Trading strategy (greeks / expiry-aware)
- Dark theme toggle
- Telegram / email alert notifications
- Live market news feed
- Multi-exchange global support (NYSE, NASDAQ, LSE)
- Backtesting engine with historical signal replay
- Docker containerisation for one-command setup
- Increase watchlist limit beyond 10
- Run multiple primary strategies side-by-side
- Real-time auto-refresh every 5 minutes during market hours
- Zerodha Kite Connect order placement integration
- Portfolio P&L tracking
- Custom alert conditions (price triggers, RSI thresholds)
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
