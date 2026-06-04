"""Central configuration: instruments, guardrails, strategy definitions.

No user cash is stored — the advisory book is driven entirely by your
'done / skipped / partial' confirmations. The automated race portfolios
rotate within the seeded capital (sell to fund a buy).
"""

# ----------------------------------------------------------------------
#  Instruments
#  qty > 0  -> seeded into every portfolio as a real starting holding
#  ai_theme -> counts toward the AI concentration cap
# ----------------------------------------------------------------------
INSTRUMENTS = [
    # --- Nordic pipeline (held) ---
    dict(ticker="AAC",     yf_symbol="AAC.ST",     name="AAC Clyde Space", market="NORDIC", currency="SEK", theme="space",     ai_theme=False, qty=7),
    dict(ticker="ABB",     yf_symbol="ABB.ST",     name="ABB",             market="NORDIC", currency="SEK", theme="industrial",ai_theme=False, qty=7),
    dict(ticker="INVE-B",  yf_symbol="INVE-B.ST",  name="Investor B",      market="NORDIC", currency="SEK", theme="holding",   ai_theme=False, qty=10),
    dict(ticker="KINV-B",  yf_symbol="KINV-B.ST",  name="Kinnevik B",      market="NORDIC", currency="SEK", theme="investment",ai_theme=False, qty=45),
    dict(ticker="NOKIA",   yf_symbol="NOKIA.HE",   name="Nokia",           market="NORDIC", currency="EUR", theme="telecom",   ai_theme=False, qty=20),
    dict(ticker="SAAB-B",  yf_symbol="SAAB-B.ST",  name="SAAB B",          market="NORDIC", currency="SEK", theme="defense",   ai_theme=False, qty=10),

    # --- US pipeline (held) ---
    dict(ticker="ACN",  yf_symbol="ACN",  name="Accenture A",          market="US", currency="USD", theme="it_services", ai_theme=False, qty=1),
    dict(ticker="AAPL", yf_symbol="AAPL", name="Apple",                market="US", currency="USD", theme="consumer_tech",ai_theme=False, qty=2),
    dict(ticker="KO",   yf_symbol="KO",   name="Coca-Cola",            market="US", currency="USD", theme="staples",     ai_theme=False, qty=2),
    dict(ticker="DELL", yf_symbol="DELL", name="Dell Technologies C",  market="US", currency="USD", theme="datacenter",  ai_theme=True,  qty=1),
    dict(ticker="NVDA", yf_symbol="NVDA", name="NVIDIA",               market="US", currency="USD", theme="ai_semi",     ai_theme=True,  qty=3),
    dict(ticker="PLTR", yf_symbol="PLTR", name="Palantir Technologies",market="US", currency="USD", theme="ai_software", ai_theme=True,  qty=8),
    dict(ticker="SNDK", yf_symbol="SNDK", name="SanDisk",              market="US", currency="USD", theme="storage",     ai_theme=True,  qty=1),
    dict(ticker="TSM",  yf_symbol="TSM",  name="Taiwan Semiconductor", market="US", currency="USD", theme="ai_semi",     ai_theme=True,  qty=1),
    dict(ticker="TSLA", yf_symbol="TSLA", name="Tesla",                market="US", currency="USD", theme="ev_auto",     ai_theme=False, qty=1),
    dict(ticker="WDC",  yf_symbol="WDC",  name="Western Digital",      market="US", currency="USD", theme="storage",     ai_theme=True,  qty=1),

    # --- AI / datacenter candidates (not held, scored for discovery) ---
    dict(ticker="CRWV", yf_symbol="CRWV", name="CoreWeave",            market="US", currency="USD", theme="ai_cloud",    ai_theme=True,  qty=0),
    dict(ticker="IREN", yf_symbol="IREN", name="IREN",                 market="US", currency="USD", theme="ai_cloud",    ai_theme=True,  qty=0),
    dict(ticker="VRT",  yf_symbol="VRT",  name="Vertiv",               market="US", currency="USD", theme="datacenter",  ai_theme=True,  qty=0),
    dict(ticker="ANET", yf_symbol="ANET", name="Arista Networks",      market="US", currency="USD", theme="datacenter",  ai_theme=True,  qty=0),
    # OMXS30 + large Nasdaq members are appended at runtime by fetch_index_members()
]

ADVISORY_ID = "advisory"          # your real, human-confirmed book
RACE_STRATEGIES = ["you", "magic_formula", "rsi2", "dual_momentum", "canslim"]

# ----------------------------------------------------------------------
#  Risk guardrails  (the single risk_config row; editable from the UI)
# ----------------------------------------------------------------------
RISK_CONFIG_DEFAULT = {
    "max_risk_per_trade_pct": 1.0,
    "max_position_pct": 5.0,
    "max_total_exposure_pct": 80.0,
    "max_theme_exposure_pct": 60.0,     # AI concentration cap (raised from 35)
    "max_trades_per_day": 3,
    "limit_orders_only": True,
    "use_leverage": False,
    "avoid_illiquid": True,
    "min_avg_daily_value": 5_000_000,   # currency units; define "liquid"
    "never_average_down": True,
    "trailing_stop_atr_mult": 3.0,      # volatility-scaled stop = mult x ATR(14)
    "probe_size_pct": 1.5,              # young / low-confidence starter
    "full_size_pct": 5.0,
    "buy_threshold": 0.60,              # composite score to act
    "halt_confidence_below": 0.50,
    "halt_consecutive_losses": 3,
    "halt_daily_loss_pct": 1.0,
    "halt_on_stale_data": True,
    "reentry_cooldown_days": 5,
    "min_courtage_pct_of_trade": 0.30,  # cost-aware entry gate
    "explain_numbers": True,            # plain-English captions on the numbers
    "portfolio_limits": True,           # TRIM/theme-cap on the advisory book; set
                                        # false if you size positions yourself
    "funds_tech_share_pct": 50,         # your funds are a mix -> est. tech share
}

# Avanza courtage (commission) model — set to your real tier
COURTAGE = {"min_fee_sek": 1.0, "pct": 0.0025, "spread_bps_by_liquidity": {"high": 5, "mid": 15, "low": 40}}

# ----------------------------------------------------------------------
#  Your strategy: five-factor core + forward-cash-flow tilt
#  valuation = 2/3 forward + 1/3 trailing, with a modest valuation overweight
# ----------------------------------------------------------------------
YOUR_STRATEGY = {
    "factor_weights": {"momentum": 1.0, "quality": 1.0, "low_vol": 0.7, "trend": 1.0, "valuation": 1.25},
    "valuation_blend": {"forward": 0.66, "trailing": 0.34},
}
