"""Factor scorer for your strategy.

build_features()  : compute raw price + fundamental factors once, with a
                    fundamentals coverage report (X/Y names with usable data).
compute_scores()  : z-score and combine into your composite (with the
                    forward-cash-flow tilt: 2/3 forward + 1/3 trailing).
write_scores()    : persist to factor_scores.

Fundamentals come from yfinance .info, which is thin for some Nordic names;
those score neutral and are reported as 'thin' rather than crashing the run.
"""
import datetime as dt
import numpy as np
import pandas as pd
import yfinance as yf

import config as C
from db import upsert, instrument_id_map


def _z(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and not np.isnan(sd) else s * 0.0


def _price_factors(prices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = {}
    for t, df in prices.items():
        c = df["Close"].dropna()
        if len(c) < 200:
            continue
        ret = c.pct_change().dropna()
        mom = (c.iloc[-21] / c.iloc[-252] - 1) if len(c) >= 252 else np.nan
        vol = ret.tail(126).std() * np.sqrt(252)
        trend = c.iloc[-1] / c.tail(200).mean() - 1
        rows[t] = dict(momentum=mom, low_vol=-vol, trend=trend)
    return pd.DataFrame(rows).T


def _fundamentals(tickers: list[str]):
    rows, status = {}, {}
    for t in tickers:
        sym = next(i["yf_symbol"] for i in C.INSTRUMENTS if i["ticker"] == t)
        try:
            info = yf.Ticker(sym).info
        except Exception:                       # noqa: BLE001
            info = {}
        pe, fpe = info.get("trailingPE"), info.get("forwardPE")
        roe, d2e = info.get("returnOnEquity"), info.get("debtToEquity")
        grow = info.get("earningsGrowth") or info.get("revenueGrowth")
        row = dict(
            trailing_yield=(1 / pe) if pe and pe > 0 else np.nan,
            forward_yield=(1 / fpe) if fpe and fpe > 0 else np.nan,
            exp_growth=grow if grow is not None else np.nan,
            roe=roe if roe is not None else np.nan,
            low_debt=(-d2e) if d2e is not None else np.nan,
        )
        rows[t] = row
        usable = sum(1 for v in (row["trailing_yield"], row["forward_yield"], row["roe"])
                     if v is not None and not (isinstance(v, float) and np.isnan(v)))
        status[t] = "ok" if usable >= 2 else "thin"
    return pd.DataFrame(rows).T, status


def build_features(prices: dict[str, pd.DataFrame]):
    """Returns (features_df, coverage). Computed once, shared by all strategies."""
    pf = _price_factors(prices)
    pf = pf[~pf.index.isin(C.BENCHMARK_TICKERS)]      # benchmarks are never scored
    fu, status = _fundamentals(list(pf.index))
    feats = pf.join(fu, how="inner")
    cov = {"ok": sum(1 for v in status.values() if v == "ok"),
           "total": len(status), "status": status}
    print(f"fundamentals {cov['ok']}/{cov['total']}")
    return feats, cov


def compute_scores(feats: pd.DataFrame) -> pd.DataFrame:
    z = pd.DataFrame(index=feats.index)
    z["momentum"] = _z(feats["momentum"])
    z["low_vol"] = _z(feats["low_vol"])
    z["trend"] = _z(feats["trend"])
    z["quality"] = (_z(feats["roe"]) + _z(feats["low_debt"])) / 2

    fwd = (_z(feats["forward_yield"]) + _z(feats["exp_growth"])) / 2
    trl = _z(feats["trailing_yield"])
    b = C.YOUR_STRATEGY["valuation_blend"]
    z["valuation"] = b["forward"] * fwd + b["trailing"] * trl
    z["fwd_cashflow"] = fwd

    w = C.YOUR_STRATEGY["factor_weights"]
    parts = [w[f] * z[f].fillna(0) for f in ("momentum", "quality", "low_vol", "trend", "valuation")]
    z["composite"] = sum(parts) / sum(w.values())
    return z.sort_values("composite", ascending=False)


def write_scores(z: pd.DataFrame):
    ids = instrument_id_map()
    today = str(dt.date.today())
    rows = [dict(instrument_id=ids[t], date=today,
                 momentum=_n(r.momentum), low_vol=_n(r.low_vol), trend=_n(r.trend),
                 quality=_n(r.quality), value=_n(r.valuation),
                 fwd_cashflow=_n(r.fwd_cashflow), composite=_n(r.composite))
            for t, r in z.iterrows() if t in ids]
    upsert("factor_scores", rows, on_conflict="instrument_id,date")
    print(f"wrote {len(rows)} factor scores")


def _n(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 4)


if __name__ == "__main__":
    from ingest import ingest_prices
    prices, _ = ingest_prices()
    feats, _ = build_features(prices)
    write_scores(compute_scores(feats))
