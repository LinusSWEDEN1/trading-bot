"""Execution engine — turns scores into recommendations and trades.

Advisory book ('advisory'): writes recommendations only and reconciles its
holdings from your 'done/skipped/partial' confirmations in user_actions. It
never trades on its own — you are the affordability check.

Race books (config.RACE_STRATEGIES): fully automated. They rotate within the
seeded capital — to fund a buy they trim the weakest holding — so no invented
cash is used. All books share the same guardrails so the comparison is clean.

Simplifications (intentional, documented): trailing stops are evaluated on the
daily close; benchmark_value is left for a later index feed; cooldown reads the
recent paper_trades log.
"""
import datetime as dt
import numpy as np
import pandas as pd

import config as C
from db import db, upsert, update, insert, fetch, instrument_id_map
from altdata import macro_regime

TODAY = str(dt.date.today())
_MARKET = {i["ticker"]: i["market"] for i in C.INSTRUMENTS}


# ---------- helpers ----------
def _atr_pct(df: pd.DataFrame, n: int = 14) -> float:
    h, l, c = df["High"], df["Low"], df["Close"].shift()
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    atr = tr.tail(n).mean()
    last = float(df["Close"].iloc[-1])
    return float(atr / last) if last else 0.05


def _courtage(value: float, liquidity: str = "mid") -> float:
    fee = max(C.COURTAGE["min_fee_sek"], value * C.COURTAGE["pct"])
    spread = value * C.COURTAGE["spread_bps_by_liquidity"].get(liquidity, 15) / 10_000
    return fee + spread


def _last_close(prices: dict[str, pd.DataFrame]) -> dict[str, float]:
    return {t: float(df["Close"].iloc[-1]) for t, df in prices.items() if len(df)}


def _recent_stop_outs(strategy_id: str, days: int) -> set[int]:
    since = str(dt.date.today() - dt.timedelta(days=days))
    rows = (db().table("paper_trades").select("instrument_id,action,date")
            .eq("strategy_id", strategy_id).gte("date", since).execute().data)
    return {r["instrument_id"] for r in rows if r["action"] in ("SELL", "STOP")}


# ---------- per-portfolio pass ----------
def _portfolio(strategy_id, prices, score, cfg, ids, info_by_ticker, advisory):
    rev = {v: k for k, v in ids.items()}
    last = _last_close(prices)
    positions = fetch("paper_positions", strategy_id=strategy_id)
    acct = fetch("accounts", strategy_id=strategy_id)
    cash = float(acct[0]["cash"]) if acct else 0.0

    held = {p["instrument_id"]: p for p in positions if p["status"] == "open"}
    def tkr(iid): return rev.get(iid)
    def px(iid): return last.get(tkr(iid))

    invested = sum((p["qty"] or 0) * (px(iid) or 0) for iid, p in held.items())
    total = invested + cash
    theme_val = sum((p["qty"] or 0) * (px(iid) or 0)
                    for iid, p in held.items()
                    if info_by_ticker.get(tkr(iid), {}).get("ai_theme"))
    theme_pct = 100 * theme_val / total if total else 0.0

    regime = macro_regime()
    buy_th = cfg["buy_threshold"]
    # advisory respects the portfolio_limits switch; race books always enforce
    # limits so the comparison stays rules-consistent
    limits_on = (not advisory) or bool(cfg.get("portfolio_limits", True))
    recs, trades = [], []
    cooldown = _recent_stop_outs(strategy_id, cfg["reentry_cooldown_days"])
    trades_today = 0

    # 1) update trailing stops on held names; fire stops
    for iid, p in list(held.items()):
        t = tkr(iid)
        if t not in prices:
            continue
        price = last[t]
        hw = max(p.get("high_water") or price, price)
        stop = hw * (1 - cfg["trailing_stop_atr_mult"] * _atr_pct(prices[t]))
        update("paper_positions",
               {"high_water": round(hw, 4), "stop_level": round(stop, 4)},
               id=p["id"])
        if price <= stop:
            recs.append(_rec(strategy_id, iid, "SELL", score.get(t, 0), price, stop,
                             0, {"reason": "trailing stop hit"}))
            if not advisory:
                cash += _sell(strategy_id, iid, p, price, "STOP", trades)
                del held[iid]

    # 2) rank candidates; generate BUY / TRIM / HOLD
    ranked = score.sort_values(ascending=False)
    for t, sc in ranked.items():
        iid = ids.get(t)
        if iid is None or t not in last:
            continue
        price = last[t]
        info = info_by_ticker.get(t, {})
        pos = held.get(iid)
        weight_pct = 100 * (pos["qty"] * price) / total if (pos and total) else 0.0

        # TRIM overweight (only when portfolio limits are on)
        if limits_on and pos and weight_pct > cfg["max_position_pct"] * 1.0:
            recs.append(_rec(strategy_id, iid, "TRIM", sc, price, pos.get("stop_level"),
                             cfg["max_position_pct"],
                             {"reason": f"{weight_pct:.0f}% > {cfg['max_position_pct']}% target",
                              "lumpy": price > total * cfg["max_position_pct"] / 100}))
            continue

        # BUY new / add
        strong = sc * regime >= buy_th
        if strong and (pos is None) and trades_today < cfg["max_trades_per_day"]:
            if iid in cooldown and sc < buy_th + 0.1:
                continue                                   # cooldown unless much stronger
            if limits_on and info.get("ai_theme") and theme_pct >= cfg["max_theme_exposure_pct"]:
                recs.append(_rec(strategy_id, iid, "HOLD", sc, price, None, 0,
                                 {"reason": "blocked: AI theme cap", "blocked": True}))
                continue
            # whole-share sizing toward probe or full size
            size_pct = cfg["probe_size_pct"] if sc < buy_th + 0.15 else cfg["full_size_pct"]
            target_val = total * size_pct / 100
            shares = int(target_val // price)
            trade_val = shares * price
            chunky = price > total * cfg["max_position_pct"] / 100
            if shares < 1:
                # one share already exceeds target -> flag chunky but still offer (advisory)
                shares, trade_val, chunky = 1, price, True
            if trade_val and _courtage(trade_val) / trade_val * 100 > cfg["min_courtage_pct_of_trade"]:
                continue                                   # cost-aware gate
            rationale = {"reason": "score above buy threshold", "size_pct": size_pct,
                         "chunky": chunky, "shares": shares}
            recs.append(_rec(strategy_id, iid, "BUY", sc, price,
                             round(price * (1 - cfg["trailing_stop_atr_mult"] * _atr_pct(prices[t])), 4),
                             size_pct, rationale, limit=round(price * 1.01, 4)))
            trades_today += 1
            if not advisory:
                cash = _buy_with_rotation(strategy_id, iid, shares, price, cash, held,
                                          ranked, ids, last, total, trades)

    # 3) advisory: apply your confirmations to holdings
    if advisory:
        _apply_user_actions(strategy_id, ids, last, trades)

    # 4) persist
    if recs:
        upsert("recommendations", recs, on_conflict="strategy_id,instrument_id,date")
    if trades:
        upsert("paper_trades", trades)
    upsert("accounts", [{"strategy_id": strategy_id, "cash": round(cash, 2)}],
           on_conflict="strategy_id")
    _write_history(strategy_id, prices, ids)


# ---------- trade primitives ----------
def _tkr_of(iid, ids):
    return next((k for k, v in ids.items() if v == iid), None)


def _rec(strat, iid, signal, score, price, stop, size_pct, rationale, limit=None):
    return dict(strategy_id=strat, instrument_id=iid, date=TODAY, signal=signal,
                composite_score=round(float(score), 4), confidence=round(float(score), 4),
                limit_price=limit, stop_level=stop, target_size_pct=size_pct,
                rationale=rationale)


def _sell(strat, iid, pos, price, action, trades):
    val = (pos["qty"] or 0) * price
    cost = _courtage(val)
    trades.append(dict(strategy_id=strat, instrument_id=iid, date=TODAY, action=action,
                       qty=pos["qty"], price=round(price, 4), cost=round(cost, 2),
                       reason=action.lower()))
    update("paper_positions", {"status": "closed", "qty": 0}, id=pos["id"])
    return val - cost


def _buy_with_rotation(strat, iid, shares, price, cash, held, ranked, ids, last, total, trades):
    """Race only: if short on cash, trim the weakest holding to fund the buy."""
    need = shares * price + _courtage(shares * price)
    if need > cash:
        for t in ranked.index[::-1]:                       # weakest first
            wid = ids.get(t)
            if wid in held and last.get(t):
                cash += _sell(strat, wid, held[wid], last[t], "SELL", trades)
                del held[wid]
                if need <= cash:
                    break
    if need <= cash:
        cost = _courtage(shares * price)
        trades.append(dict(strategy_id=strat, instrument_id=iid, date=TODAY, action="BUY",
                           qty=shares, price=round(price, 4), cost=round(cost, 2), reason="signal"))
        insert("paper_positions", [dict(strategy_id=strat, instrument_id=iid, qty=shares,
                                        market=_MARKET.get(_tkr_of(iid, ids)),
                                        avg_price=round(price, 4), high_water=round(price, 4),
                                        opened_at=TODAY, status="open")])
        cash -= shares * price + cost
    return cash


def _apply_user_actions(strat, ids, last, trades):
    rows = fetch("user_actions", rec_date=TODAY)
    for a in rows:
        iid = a["instrument_id"]
        if a["acted"] in ("done", "partial") and a.get("qty"):
            t = next((k for k, v in ids.items() if v == iid), None)
            price = last.get(t)
            existing = [p for p in fetch("paper_positions", strategy_id=strat, instrument_id=iid)
                        if p["status"] == "open"]
            if existing:
                p = existing[0]
                update("paper_positions", {"qty": (p["qty"] or 0) + a["qty"]}, id=p["id"])
            else:
                insert("paper_positions", [dict(strategy_id=strat, instrument_id=iid, qty=a["qty"],
                                                market=_MARKET.get(t),
                                                avg_price=price, high_water=price, opened_at=TODAY,
                                                status="open")])
            if price:
                trades.append(dict(strategy_id=strat, instrument_id=iid, date=TODAY, action="USER",
                                   qty=a["qty"], price=round(price, 4), cost=0, reason="confirmed"))


def _write_history(strat, prices, ids):
    rev = {v: k for k, v in ids.items()}
    last = _last_close(prices)
    acct = fetch("accounts", strategy_id=strat)
    cash = float(acct[0]["cash"]) if acct else 0.0
    for market in ("US", "NORDIC"):
        positions = [p for p in fetch("paper_positions", strategy_id=strat)
                     if p["status"] == "open" and p["market"] == market]
        value = sum((p["qty"] or 0) * (last.get(rev.get(p["instrument_id"])) or 0) for p in positions)
        upsert("portfolio_history",
               [dict(strategy_id=strat, date=TODAY, market=market,
                     value=round(value, 2), cash=round(cash, 2), benchmark_value=None, drawdown=None)],
               on_conflict="strategy_id,date,market")


# ---------- entry point ----------
def run_all(prices: dict[str, pd.DataFrame], scores: dict[str, pd.Series]):
    cfg = fetch("risk_config", id=1)[0]["config"]
    ids = instrument_id_map()
    info_by_ticker = {i["ticker"]: i for i in C.INSTRUMENTS}

    _portfolio(C.ADVISORY_ID, prices, scores["you"], cfg, ids, info_by_ticker, advisory=True)
    for strat in C.RACE_STRATEGIES:
        _portfolio(strat, prices, scores[strat], cfg, ids, info_by_ticker, advisory=False)
    print("engine complete")
