"""Data spine — run first, then daily.

  seed_instruments() / seed_config() / seed_positions()  : one-time setup
  ingest_prices()  : daily OHLCV via yfinance -> prices table

Resilience: each ticker is fetched independently. A ticker that fails or
returns stale data is dropped from today's scoring (the NAME is skipped,
not the whole run) and recorded in the coverage report. Only a catastrophic
price loss (less than half the universe) halts the run.
"""
import datetime as dt
import pandas as pd
import yfinance as yf

import config as C
from db import upsert, instrument_id_map, fetch


def seed_instruments():
    rows = [{k: inst[k] for k in
             ("ticker", "yf_symbol", "name", "market", "currency", "theme", "ai_theme")}
            | {"is_held": inst["qty"] > 0, "is_watch": True}
            for inst in C.INSTRUMENTS]
    upsert("instruments", rows, on_conflict="ticker")
    print(f"seeded {len(rows)} instruments")


def seed_config():
    if not fetch("risk_config", id=1):
        upsert("risk_config", [{"id": 1, "config": C.RISK_CONFIG_DEFAULT}])
        print("wrote default risk_config")


def seed_positions():
    ids = instrument_id_map()
    held = [inst for inst in C.INSTRUMENTS if inst["qty"] > 0]
    strategies = [C.ADVISORY_ID] + C.RACE_STRATEGIES
    rows = []
    for strat in strategies:
        for inst in held:
            rows.append(dict(
                strategy_id=strat, instrument_id=ids[inst["ticker"]],
                market=inst["market"], qty=inst["qty"],
                avg_price=None, high_water=None, stop_level=None,
                opened_at=str(dt.date.today()), status="open"))
    upsert("paper_positions", rows)
    upsert("accounts",
           [{"strategy_id": C.ADVISORY_ID, "is_advisory": True, "cash": 0}] +
           [{"strategy_id": s, "is_advisory": False, "cash": 0} for s in C.RACE_STRATEGIES],
           on_conflict="strategy_id")
    print(f"seeded positions for {len(strategies)} portfolios")


def ingest_prices(lookback_days: int = 420):
    """Returns (prices, coverage). Drops failed/stale tickers; halts only if
    fewer than half the universe returns usable, recent data."""
    ids = instrument_id_map()
    symbols = {inst["ticker"]: inst["yf_symbol"] for inst in C.INSTRUMENTS}
    start = dt.date.today() - dt.timedelta(days=lookback_days)
    out, rows, status = {}, [], {}
    for ticker, sym in symbols.items():
        try:
            df = yf.download(sym, start=str(start), progress=False, auto_adjust=True)
            if df.empty:
                status[ticker] = "empty"
                continue
            if isinstance(df.columns, pd.MultiIndex):      # single-symbol flatten
                df.columns = df.columns.get_level_values(0)
            newest = df.index.max().date()
            if (dt.date.today() - newest).days > 4:
                status[ticker] = "stale"                   # skip this name today
                continue
            df = df.tail(lookback_days)
            out[ticker] = df
            for date, r in df.iterrows():
                rows.append(dict(instrument_id=ids[ticker], date=str(date.date()),
                                 open=_f(r["Open"]), high=_f(r["High"]), low=_f(r["Low"]),
                                 close=_f(r["Close"]), volume=int(_f(r["Volume"]) or 0)))
            status[ticker] = "ok"
        except Exception as e:                             # noqa: BLE001
            status[ticker] = f"error: {str(e)[:60]}"
    upsert("prices", rows, on_conflict="instrument_id,date")

    total, got = len(symbols), len(out)
    if got < max(3, 0.5 * total):
        raise RuntimeError(f"Price feed catastrophic: only {got}/{total} usable. Halting.")
    coverage = {"got": got, "total": total, "status": status}
    print(f"prices {got}/{total}")
    return out, coverage


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    seed_instruments(); seed_config(); seed_positions(); ingest_prices()
