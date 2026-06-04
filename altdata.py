"""Alt-data layer — interface ready, leaf sources stubbed.

The engine and your strategy read whatever rows exist in alt_signals for today
and fold them in as labelled tilts, so you can populate sources incrementally
(insider first) and they flow into scoring with no other change.

refresh_all() returns a coverage dict for the run's health report. Each leaf,
once implemented, should return "X/Y" instead of "off"; until then they are
safe no-ops and never crash the run.
"""
import datetime as dt
from db import db, instrument_id_map


def alt_tilt_by_ticker(tickers: list[str], date: str | None = None) -> dict[str, float]:
    date = date or str(dt.date.today())
    ids = instrument_id_map()
    rev = {v: k for k, v in ids.items()}
    rows = db().table("alt_signals").select("instrument_id,score,weight").eq("date", date).execute().data
    tilt = {t: 0.0 for t in tickers}
    for r in rows:
        t = rev.get(r["instrument_id"])
        if t in tilt and r.get("score") is not None:
            tilt[t] += float(r["score"]) * float(r.get("weight") or 1.0)
    return tilt


def macro_regime() -> float:
    return 1.0      # TODO: FRED yield curve / unemployment + GDELT tone


def fetch_insider(date=None): return "off"            # SEC Form 4 + EU MAR
def fetch_price_targets(date=None): return "off"      # Finnhub / FMP
def fetch_congress(date=None): return "off"           # Senate Stock Watcher / Quiver
def fetch_report_sentiment(date=None): return "off"   # EDGAR full-text + FinBERT


def refresh_all(date=None) -> dict:
    """Run each leaf source; never let one break the run. Returns coverage."""
    cov = {}
    for fn, key in ((fetch_insider, "insider"), (fetch_price_targets, "targets"),
                    (fetch_congress, "congress"), (fetch_report_sentiment, "reports")):
        try:
            cov[key] = fn(date)
        except Exception as e:                          # noqa: BLE001
            cov[key] = f"error: {str(e)[:40]}"
            print(f"alt-data {key} skipped: {e}")
    return cov
