"""Daily orchestrator — the GitHub Actions entry point.

  python run.py --setup     run once: create config rows, seed holdings
  python run.py             daily : ingest -> features -> strategies -> engine

Every layer reports an X/Y coverage count into one run_log row. Broken
optional sources are skipped and shown, not crashed. A catastrophic price
failure raises, which writes a halted run_log and fails the job loudly.
"""
import sys
import ingest
import factors
import strategies
import engine
import altdata
from db import upsert


def setup():
    ingest.seed_instruments()
    ingest.seed_config()
    ingest.seed_positions()
    print("setup complete — now run without --setup for the daily loop")


def daily():
    prices, price_cov = ingest.ingest_prices()          # raises only if catastrophic
    alt_cov = altdata.refresh_all()
    feats, fund_cov = factors.build_features(prices)
    you_z = factors.compute_scores(feats)
    factors.write_scores(you_z)
    scores = strategies.score_all(prices, feats, you_z)
    engine.run_all(prices, scores)

    feeds = {"prices": f"{price_cov['got']}/{price_cov['total']}",
             "fundamentals": f"{fund_cov['ok']}/{fund_cov['total']}"}
    feeds.update({f"alt:{k}": v for k, v in alt_cov.items()})
    complete = (price_cov["got"] == price_cov["total"] and
                fund_cov["ok"] == fund_cov["total"])
    upsert("run_log", [dict(ok=complete, feeds=feeds, halted=False, halt_reason=None)])
    print("daily run complete", feeds)


if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup()
    else:
        try:
            daily()
        except Exception as e:                          # noqa: BLE001
            upsert("run_log", [dict(ok=False, feeds={"error": str(e)[:200]},
                                    halted=True, halt_reason=str(e)[:200])])
            raise
