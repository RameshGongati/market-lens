# 📈 Market Lens

A local stock market analysis application built with Python and Streamlit. Market Lens lets you build custom watchlists and analyse stocks with a **two-axis model** — pick a **Trading Type** (time horizon) and a **Primary Strategy** (Demand/Supply Zones or Trend Following), then layer optional **ODD Enhancers** on top — all powered by real-time data from multiple configurable sources.

The app is a **multi-page Streamlit application**: a Market Overview dashboard, a full Market Heatmap, scan results, per-stock detail charts, a Chart Pattern Scanner, an Alerts feed with Telegram delivery, an F&O Results monitor, watchlist management, and Settings.

---

## Features

- **Stock Search with Autocomplete** — Search from 2,374 NSE-listed stocks by symbol or company name; exchange is auto-filled on selection
- **Custom Watchlists** — Create up to 10 watchlists, each holding up to 10 stocks (NSE / BSE)
- **Predefined Index Watchlists** — One-click access to 10 NSE index watchlists: Nifty 50, Nifty Next 50, Nifty Auto, Nifty Bank, Nifty IT, Nifty Pharma, Nifty Metal, Nifty Energy, Nifty FMCG, and F&O Stocks (208 options-eligible stocks). Toggle between "My Watchlists" and "Index Watchlists" with a horizontal radio in the sidebar; the lists themselves can be refreshed from live NSE data via the sidebar
- **Screener** — Collapsible multi-criteria screener in the sidebar to filter analysis results by: Proximity to Zone (≤3% / ≤5% / ≤10%), Min ODD Score (7 / 6+ / 5+), and Zone Strength (Normal / Strong / Very Strong). Filters combine with AND logic and apply instantly without re-running analysis
- **Multiple Data Sources** — Yahoo Finance (default, no auth) and Jugaad Data (NSE direct) both fully working; NSE India, Zerodha Kite Connect, Upstox API and TradingView are scaffolded but not yet functional
- **Market Heatmap** — 20 index/sector group tiles with live change %, drill-down to per-group stock tiles, gainers/losers/with-setups filters, and scan-setup overlays; reachable from the dashboard and via shareable `?heatmap_group=` links
- **Chart Pattern Scanner** — an independent pipeline (separate from the zone engine) detecting Triangles (symmetrical/ascending/descending), VCP / Tight Base, Range Breakouts, Flags / Pennants and Double Tops / Bottoms across a watchlist, with stage (Forming / Near Apex / Breakout Confirmed), confidence, breakout bias and zone context; results are cached in SQLite so pattern deep links survive new tabs
- **Telegram Alerts** — zone-proximity notifications via the Telegram Bot API: full setup UI on the Settings page (bot token, recipients, conditions, cooldown modes) plus a standalone background monitor (`alert_monitor.py`) that keeps scanning during market hours even with the app closed; an in-app Alerts page merges live matches with the delivery history
- **F&O Results Monitor** — Reports page tracking earnings dates, EPS/revenue estimates, surprises and price reactions across the F&O universe, with timing filters and a per-sector reaction heatmap (disk-cached; fetch only on explicit Refresh)
- **Zone Confirmation Screener** — opt-in mode surfacing stocks where price entered a zone and closed back out (evidence of a real reaction), drawn as CONFIRMED zones on the chart
- **Two-Axis Analysis Model** — choose independently along two axes from the sidebar:
  - **Trading Type** (time horizon, sets the default candle timeframe): Options Trading, Intraday Trading, Short-term Trading, Long-term Investment
  - **Primary Strategy** (the base method):
    - **Demand/Supply Zones** — institutional legin/base/legout zone detection with the 7-point ODD trade score and a 50-SMA "clock method" trend filter
    - **Trend Following (SMA50/EMA20)** — 50/200-SMA golden-cross / death-cross signals (BUY / SELL / HOLD) with trend context
  - **ODD Enhancers** (optional, multi-select): Fibonacci Confluence, EMA 20 Confluence, RSI _(RSI is selectable but not yet wired into scoring)_
- **Confidence / Strength Rating** — Strong / Medium / Weak badge on every stock card and detail view, derived from the active strategy's signal conviction
- **Candle-Interval Selector** — On the detail chart, switch the candle interval (Daily / Weekly / Monthly / 75m / 15m) independently of the trading type; changing it re-fetches data **and** re-runs the analysis at that interval so the overlays stay consistent (75m is resampled from 15m; intraday falls back to Daily when unavailable)
- **Period Selector** — Dropdown with 1W to 5Y period options; daily data fetches up to 5 years of history
- **Zone Drawing from Formation Point** — Zone rectangles start from where the zone formed (base candles) rather than spanning the full chart width
- **Crosshair with Price & Date Labels** — Interactive crosshair with floating price label on y-axis and date label; labels persist correctly across period and interval changes
- **Candlestick & Line Chart Toggle** — Switch between candlestick and line chart; volume and RSI subplots included
- **Colour-coded Stock Cards** — Company name, current price, absolute + percentage change, strength badge, and last-updated timestamp
- **Market Status Indicator** — Live IST clock, green/red open/closed banner, and countdown to next open or close in the sidebar
- **Analysis History** — Every run is preserved in the local database; a timeline table on the detail view shows the last 7 results with trend direction (improving / deteriorating / stable)
- **Personal Notes per Stock** — Add, view, and delete timestamped notes on the stock detail page
- **Filter & Sort Results** — The Analysis Results page filters by status (Bullish / Bearish / Neutral) and strength (Strong / Medium / Weak) with removable screener chips, a ranked table with search and paging, and per-row View deep links. Screener filters (proximity, score, zone strength) layer on top
- **Export Analysis Results** — Download a three-sheet Excel workbook (Summary, Details, Alerts) or a formatted PDF report. Exports adapt to the active strategy (zone rows for Demand/Supply, signal/cross rows for Trend Following) and save to your **Windows Downloads** folder (`Downloads/market-lens`) when running under WSL, falling back to `~/market-lens-exports`
- **Persistent Preferences** — Sidebar selections (watchlist, data source, trading type, strategy, enhancers) are saved to `~/.market-lens/user_preferences.json` and **restored on every launch**; the last completed scan is also snapshotted to SQLite so results survive a new tab or session
- **Interactive Charts** — Plotly charts with zone overlays, SMA/VWAP series, and RSI subplot; scan progress card with five selectable styles
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
| Data — NSE direct | jugaad-data _(installed in the venv; not yet listed in requirements.txt)_ |
| Data — NSE scrape | requests + BeautifulSoup4 _(scaffold)_ |
| Data — Zerodha | kiteconnect _(scaffold)_ |
| Data — Upstox | upstox-python-sdk _(scaffold)_ |
| Data — TradingView | tvdatafeed _(pending — no stable library; app links out to tradingview.com)_ |
| Data processing | pandas, numpy |
| Alerts | Telegram Bot API (requests) |
| Encryption | cryptography (Fernet) |
| Database | SQLite (stdlib, 7 tables) |
| Excel export | openpyxl |
| PDF export | reportlab |
| Logging | Python stdlib logging |

---

## Installation

### Prerequisites

- Python 3.12
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

# 4. Install the NSE-direct data source (not yet in requirements.txt)
pip install jugaad-data

# 5. (Optional) Copy the example environment file
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
├── app.py                     # Streamlit entry point: session state, deep-link handling, page routing
├── alert_monitor.py           # Standalone background alert monitor (runs outside Streamlit)
├── requirements.txt
├── README.md
├── CLAUDE.md                  # Full developer context: conventions, GTF rules, gotchas
├── docs/                      # architecture.md, requirements.md (GTF roadmap), REFINEMENT_PLAN.md
├── config/
│   ├── settings.py            # Global constants, data sources, limits
│   ├── trading_config.py      # Two-axis model: trading types, strategies, enhancers, timeframes
│   ├── credentials.py         # Encrypted credential store
│   ├── alert_settings.py      # Telegram alert config load/save (config/alert_config.json, gitignored)
│   └── preferences.py         # User preference persistence (+ old-schema migration)
├── data/
│   ├── stock_list.json        # 2,374 NSE-listed stocks for autocomplete
│   ├── predefined_watchlists.json  # NSE index watchlists (Nifty 50, sectors, F&O, etc.)
│   ├── manager.py             # DataSourceManager: source switching, timeframe-aware fetching
│   ├── nse_bhavcopy.py        # NSE end-of-day file: repairs unfinished/missing bars
│   ├── market_indices.py      # NIFTY 50 / BANK NIFTY snapshots + market bias
│   ├── market_heatmap.py      # Heatmap groups, batched quotes, basket fallback
│   ├── nse_indices.py         # Refreshes predefined watchlists from live NSE
│   ├── earnings_calendar.py   # Results calendar, disk-cached daily
│   └── sources/               # DataSource ABC + yahoo_finance, jugaad (working);
│                              #   nse_india, zerodha, upstox, tradingview (scaffolds)
├── analysis/
│   ├── base.py                # Abstract BaseAnalysis + Strength type
│   ├── demand_supply.py       # Zone-engine orchestrator: detection → scoring → filtering → enrichment
│   ├── trend_following.py     # 50/200 SMA golden-cross / death-cross strategy
│   ├── zone_engine/           # GTF engine: candles, patterns, scoring, models, filters,
│   │                          #   trend (50-SMA clock), EMA20 + Fibonacci enhancers
│   ├── pattern_models.py      # PatternMatch / PatternPoint (Chart Pattern Scanner)
│   ├── pattern_scanner.py     # Pattern scan orchestration + result filters + export rows
│   └── pattern_detectors/     # Triangles, VCP, range breakouts, flags/pennants, doubles
├── watchlist/
│   ├── models.py              # Watchlist & Stock dataclasses
│   └── manager.py             # CRUD with limits enforced
├── ui/
│   ├── components/
│   │   ├── panels.py          # Shared page surfaces (cards, chips, pagination, scan progress)
│   │   ├── stock_detail.py    # Detail page: 7 tabs, chart, setup rail, history, notes
│   │   ├── stock_card.py      # Deep-link builder for View links
│   │   ├── watchlist_panel.py # Watchlist management + autocomplete search
│   │   ├── sidebar.py         # Two-axis controls, market status, watchlist picker, nav
│   │   ├── tradingview_chart.py  # TradingView deep-link placeholder
│   │   ├── credentials_form.py
│   │   └── notifications.py
│   └── pages/
│       ├── dashboard.py       # Scan engine + shared helpers (not a routed page itself)
│       ├── market_overview.py # Dashboard landing page
│       ├── market_heatmap.py  # Full heatmap page
│       ├── analysis_results.py # Scan results page (runs the scan, saves the snapshot)
│       ├── alerts_page.py     # Alerts feed (live matches + Telegram history)
│       ├── reports_page.py    # F&O results monitor
│       ├── pattern_scanner.py / pattern_results.py / pattern_detail.py / pattern_common.py
│       ├── watchlist_manager.py
│       ├── placeholders.py    # Trade Journal (awaiting requirements)
│       └── settings.py        # Status strip, Telegram setup, monitor control, data management
├── alerts/
│   ├── telegram.py            # Telegram Bot API delivery + message formatting
│   ├── zone_alert_checker.py  # Zone-proximity matching over cached results
│   ├── manager.py             # In-app alert trigger logic
│   └── monitor_control.py     # flock-based monitor status + start/stop
├── storage/
│   └── database.py            # SQLite CRUD (7 tables: watchlists, stocks, analysis_results,
│                              #   latest_analysis_snapshot, alerts, stock_notes, pattern_scans)
├── tests/                     # 481 pytest tests across 22 files
└── utils/
    ├── logger.py              # File + console logging
    ├── helpers.py             # format_currency, format_timestamp, search_stocks
    ├── market_hours.py        # NSE market hours, IST clock, countdown
    ├── system_info.py         # App-dir disk footprint + cache clearing
    └── export.py              # Excel (openpyxl) and PDF (reportlab) export
```

---

## Data Sources Explained

| Source | Status | Auth Required | Notes |
|---|---|---|---|
| **Yahoo Finance** | ✅ Working | No | Default source; `.NS` suffix for NSE, `.BO` for BSE. Prices fetched **unadjusted** (`auto_adjust=False`) so zone levels match TradingView/Kite |
| **Jugaad Data (NSE)** | ✅ Working | No | Reads NSE directly; more reliable recent sessions than Yahoo. Requires `pip install jugaad-data` |
| **NSE India** | Scaffold | No | Scrapes NSE website; not functional yet |
| **Zerodha Kite Connect** | Scaffold | api_key, api_secret, access_token | Requires Kite Connect developer account |
| **Upstox API** | Scaffold | api_key, api_secret, access_token | Requires Upstox developer account |
| **TradingView** | Scaffold | username, password | tvdatafeed has no stable release; the app links out to tradingview.com instead |

Credentials are entered via the sidebar form and stored encrypted at `~/.market-lens/credentials.json`. They are never committed to version control.

---

## Known Limitations & Roadmap

**Current limitations (honest status):**

- **Trade Plan fields are placeholders** — entry, stop-loss, targets, risk/reward and position size render as "Phase 2 pending"; GTF Phase 2 (M1/M7/M29) is the next implementation phase
- **RSI enhancer** is selectable in the sidebar but **not yet wired** into scoring — selecting it currently has no effect on the analysis
- **Options Trading** trading type is available as a time horizon, but a dedicated options-specific strategy/spec is still **pending** (it currently uses the chosen primary strategy on daily data)
- **Intraday data** (15m / 75m) is **limited by data providers** for Indian stocks — when unavailable the app falls back to Daily candles with a notice
- **Alerts persist only for user watchlists** — predefined index/F&O scans surface live matches but write no history rows (their stocks carry no database id)
- **Trade Journal** page is routed but awaiting requirements (renders a placeholder)
- **TradingView** full data integration is pending a stable library; the app links out to tradingview.com in the meantime

**Planned features:**

- GTF Phase 2: entry / stop-loss / target computation (M1), ATR volatility buffer (M7), Entry Types 1/2/3 (M29)
- Multi-timeframe (HTF/ITF/LTF) analysis — GTF Phase 3
- Wire up the RSI enhancer into the confluence scoring
- Dedicated Options Trading strategy (greeks / expiry-aware)
- Dark theme toggle
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

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes following the existing code style (PEP 8, type hints, docstrings)
4. Open a pull request describing what changed and why

---

## License

MIT License — see [LICENSE](LICENSE) for details.
