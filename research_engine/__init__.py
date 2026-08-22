# Market Lens Research Engine.
#
# A layer for research and scanner design that is deliberately SEPARATE from
# the production zone engine and scanners:
#   harness/   — offline backtesting (fetch → detect → simulate → aggregate).
#                Contains a BACKTEST-ONLY zone-engine patch behind
#                enable_backtest_mode(); nothing outside harness/ may call it.
#   store.py   — its own SQLite database (~/.market-lens/research_engine.db).
#   importer.py— loads generated research outputs into the store as "runs".
#
# The UI (ui/pages/research_page.py) reads ONLY from store.py. It must never
# import harness modules, so research behaviour can never leak into the app.
