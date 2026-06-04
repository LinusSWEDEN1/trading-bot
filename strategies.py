"""Five strategy scorers for the horse race.

Each returns a pandas Series of scores in [0, 1] indexed by ticker (higher =
stronger buy). They share the engine's single buy_threshold, so the race
measures signal quality, not different thresholds.

score_all() takes the features and your composite already computed by
factors.build_features / compute_scores (no recomputation, no double fetch).
"""
import numpy as np
import pandas as pd

import factors
from altdata import alt_tilt_by_ticker


def _pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True).fillna(0.0)


def _rsi(close: pd.Series, n: int = 2) -> float:
    d = close.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def score_all(prices: dict[str, pd.DataFrame], feats: pd.DataFrame,
              you_z: pd.DataFrame) -> dict[str, pd.Series]:
    tickers = list(prices.keys())
    out: dict[str, pd.Series] = {}

    # you: your composite + any stored alt-data tilts (0 until populated)
    you = you_z["composite"].copy()
    tilts = alt_tilt_by_ticker(you.index.tolist())
    you = you.add(pd.Series(tilts), fill_value=0)
    out["you"] = _pct_rank(you)

    # magic_formula: earnings yield + ROE
    out["magic_formula"] = _pct_rank(factors._z(feats["trailing_yield"]) + factors._z(feats["roe"]))

    # rsi2: oversold while above the 200-day average
    rsi_score = {}
    for t, df in prices.items():
        c = df["Close"].dropna()
        if len(c) < 200:
            continue
        above = c.iloc[-1] > c.tail(200).mean()
        rsi_score[t] = (10 - min(_rsi(c, 2), 10)) / 10 if above else 0.0
    out["rsi2"] = pd.Series(rsi_score)

    # dual_momentum: relative momentum gated by positive absolute momentum
    dm = feats["momentum"].copy()
    dm[dm < 0] = np.nan
    out["dual_momentum"] = _pct_rank(dm)

    # canslim: growth + relative strength + proximity to 52-week high
    near_high = {}
    for t, df in prices.items():
        c = df["Close"].dropna()
        if len(c) >= 60:
            near_high[t] = c.iloc[-1] / c.tail(252).max()
    cs = (factors._z(feats["exp_growth"]) + factors._z(feats["momentum"]) +
          factors._z(pd.Series(near_high)))
    out["canslim"] = _pct_rank(cs)

    return {k: v.reindex(tickers).fillna(0.0) for k, v in out.items()}
