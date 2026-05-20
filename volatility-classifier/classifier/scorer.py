from __future__ import annotations

import math

import yfinance as yf
from rich.console import Console

console = Console()

# Strike thresholds (per CLAUDE.md):
#   0-1 → GREEN, 2 → YELLOW, 3+ → RED
GREEN_MAX = 1
YELLOW_MAX = 2

# --- technical strike thresholds ---
YIELD_BPS_STRIKE = 10.0          # |yield move| beyond this → strike
YIELD_ACCEL_BPS_STRIKE = 5.0     # |yield move| beyond this AND accelerating → strike
SMH_VS_SPY_STRIKE = -1.0
SMH_VS_QQQ_STRIKE = -0.5
SEMI_HEALTH_STRIKE = 40          # semi_health_score below this → strike
SEMI_LAGGARD_PCT = -0.75         # a semi name this far below SPY counts as a laggard
SEMI_LAGGARD_COUNT_STRIKE = 2    # this many laggards → strike

# --- sentiment strike thresholds ---
SEMI_SENTIMENT_STRIKE = -0.3
MACRO_SENTIMENT_STRIKE = -0.3

# --- automatic RED override thresholds ---
EARNINGS_SURPRISE_RED = 10.0     # |earnings surprise| beyond this → auto RED

# --- compute_semi_health weighting (per the spec) ---
SEMI_NAMES = ["NVDA", "AMD", "TSM", "ASML", "INTC"]
NAME_WEIGHT = 8.0           # per individual semi green vs SPY → 5 × 8 = 40%
RS_FULL = 25.0             # SMH outperforming QQQ
RS_PARTIAL = 12.0         # SMH underperforming QQQ by 0-0.5%
IV_FULL = 20.0            # NVDA IV not elevated
EARNINGS_BASE = 15.0
EARNINGS_BEAT_BONUS = 10.0
EARNINGS_MISS_PENALTY = 15.0
EARNINGS_GUIDANCE_PENALTY = 20.0
EARNINGS_MIN, EARNINGS_MAX = 0.0, 30.0   # bonuses can push past 15, capped at 30
EARNINGS_SURPRISE_THRESHOLD = 5.0        # |surprise_pct| beyond this is a beat/miss


# --------------------------------------------------------------------------
# Directional bias engine
# --------------------------------------------------------------------------

# Component weights — final score is the sum, clamped to [-100, +100].
BIAS_SEMI_SENTIMENT_W = 25.0
BIAS_MACRO_SENTIMENT_W = 20.0
BIAS_SMH_VS_SPY_W = 15.0
BIAS_SMH_VS_QQQ_W = 10.0
BIAS_YIELD_W = 10.0
BIAS_DXY_W = 8.0           # ±8 — dollar strength is a tech/MNQ headwind
BIAS_VIX_SPREAD_W = 5.0    # ±5 soft signal — near-term fear vs calm

# Inputs saturate at these magnitudes (linear ramp until then).
BIAS_SMH_SPY_SAT = 1.0   # ±1.0% — caps SMH-vs-SPY contribution
BIAS_SMH_QQQ_SAT = 1.0
BIAS_YIELD_BPS_SAT = 5.0
BIAS_DXY_SAT = 0.3       # ±0.3% — caps the DXY contribution at ±8


def _ramp(value: float, saturation: float) -> float:
    """Map a signed input to [-1, +1] with a linear ramp out to `saturation`."""
    if saturation <= 0:
        return 0.0
    return max(-1.0, min(1.0, value / saturation))


def _bias_label(score: float) -> str:
    if score > 40:
        return "Bullish"
    if score >= 15:
        return "Lean Bullish"
    if score >= -15:
        return "Neutral"
    if score > -40:
        return "Lean Bearish"
    return "Bearish"


def _bias_conviction(score: float) -> str:
    mag = abs(score)
    if mag > 40:
        return "High"
    if mag >= 20:
        return "Moderate"
    return "Low"


def _bias_reason(label: str, contributions: list[tuple[str, float, str]]) -> str:
    """Plain-English sentence naming the two biggest absolute drivers."""
    ranked = sorted(contributions, key=lambda c: abs(c[1]), reverse=True)
    drivers = [c for c in ranked if abs(c[1]) > 0.5][:2]
    if not drivers:
        return f"{label} — no input pushed strongly in either direction."
    details = " and ".join(c[2] for c in drivers)
    return f"Driven by {details}."


def compute_bias(market: dict, options: dict, sentiment: dict, regime: dict) -> dict:
    """Forward-looking directional bias score in [-100, +100].

    Aggregates overnight sentiment, semi relative strength, and the yield
    move, then nudges by GEX and the HMM regime. Chaotic regime hard-
    overrides to "No Bias" — there is no sustained direction to lean on.
    """
    market = market or {}
    options = options or {}
    sentiment = sentiment or {}
    regime = regime or {}

    regime_label = regime.get("regime_label")
    if regime_label == "Chaotic":
        return {
            "bias_score": 0.0,
            "bias_label": "No Bias",
            "bias_conviction": "None",
            "bias_reason": "Chaotic regime — no directional edge",
        }

    contributions: list[tuple[str, float, str]] = []
    running = 0.0

    semi_sent = sentiment.get("semi_sentiment_score")
    if semi_sent is not None:
        c = float(semi_sent) * BIAS_SEMI_SENTIMENT_W
        running += c
        descriptor = "strong semi sentiment" if semi_sent > 0 else "weak semi sentiment"
        contributions.append(("semi_sentiment", c, f"{descriptor} ({semi_sent:+.2f})"))

    macro_sent = sentiment.get("macro_sentiment_score")
    if macro_sent is not None:
        c = float(macro_sent) * BIAS_MACRO_SENTIMENT_W
        running += c
        descriptor = "positive macro tone" if macro_sent > 0 else "negative macro tone"
        contributions.append(("macro_sentiment", c, f"{descriptor} ({macro_sent:+.2f})"))

    smh_vs_spy = market.get("smh_vs_spy")
    if smh_vs_spy is not None:
        c = _ramp(float(smh_vs_spy), BIAS_SMH_SPY_SAT) * BIAS_SMH_VS_SPY_W
        running += c
        verb = "outperforming" if smh_vs_spy > 0 else "underperforming"
        contributions.append(
            ("smh_vs_spy", c, f"SMH {verb} SPY by {smh_vs_spy:+.1f}%")
        )

    smh_vs_qqq = market.get("smh_vs_qqq")
    if smh_vs_qqq is not None:
        c = _ramp(float(smh_vs_qqq), BIAS_SMH_QQQ_SAT) * BIAS_SMH_VS_QQQ_W
        running += c
        verb = "outperforming" if smh_vs_qqq > 0 else "underperforming"
        contributions.append(
            ("smh_vs_qqq", c, f"SMH {verb} QQQ by {smh_vs_qqq:+.1f}%")
        )

    yield_bps = market.get("yield_bps_change")
    if yield_bps is not None:
        # Higher yields are risk-off for tech/MNQ, so contribution is inverted.
        c = -_ramp(float(yield_bps), BIAS_YIELD_BPS_SAT) * BIAS_YIELD_W
        running += c
        verb = "rising" if yield_bps > 0 else "falling"
        contributions.append(
            ("yield_bps_change", c, f"yields {verb} {yield_bps:+.1f}bps")
        )

    dxy_change = market.get("dxy_change")
    if dxy_change is not None:
        # A stronger dollar is a headwind for tech/MNQ — contribution inverted.
        # Ramp saturates at ±0.3%, so |contribution| caps at BIAS_DXY_W (8).
        c = -_ramp(float(dxy_change), BIAS_DXY_SAT) * BIAS_DXY_W
        running += c
        verb = "strengthening" if dxy_change > 0 else "weakening"
        contributions.append(
            ("dxy_change", c, f"dollar {verb} ({dxy_change:+.2f}%)")
        )

    # VIX9D − VIX spread — soft signal: near-term fear leans bearish.
    vix_spread = market.get("vix_spread")
    if vix_spread is not None:
        if vix_spread > 1.0:
            c = -BIAS_VIX_SPREAD_W
        elif vix_spread < -1.0:
            c = BIAS_VIX_SPREAD_W
        else:
            c = 0.0
        if c != 0.0:
            running += c
            descriptor = (
                "elevated near-term fear" if c < 0 else "near-term calm"
            )
            contributions.append(
                ("vix_spread", c, f"{descriptor} (VIX9D−VIX {vix_spread:+.1f})")
            )

    # GEX adjustment — amplifying gamma stretches directional moves.
    gex_label = (options.get("gex_label") or "").lower()
    if gex_label == "amplifying":
        running *= 1.2

    # Regime adjustment — dampen mean-reverting regimes, boost trending-low-vol.
    if regime_label == "Trending Low Vol":
        running *= 1.1
    elif regime_label == "Mean Reverting":
        running *= 0.5

    score_final = max(-100.0, min(100.0, running))
    label = _bias_label(score_final)
    conviction = _bias_conviction(score_final)
    reason = _bias_reason(label, contributions)

    return {
        "bias_score": round(score_final, 1),
        "bias_label": label,
        "bias_conviction": conviction,
        "bias_reason": reason,
    }


# --------------------------------------------------------------------------
# Open bias — likely character of the first 30-60 minutes after 9:30 ET
# --------------------------------------------------------------------------

OPEN_BIAS_DISCLAIMER = "Pre-market estimate only — conditions change at open"

GAP_THRESHOLD_PCT = 0.3
SMH_UNDERPERFORM_PCT = -0.5
GEX_MAGNET_PCT = 0.5
TRADING_DAYS = 252


def _fetch_mnq_premarket() -> dict:
    """MNQ=F premarket price, prior close, and overnight high/low from yfinance.

    Returns Nones on failure so callers can label fields as ESTIMATED.
    """
    result: dict = {
        "price": None,
        "prior_close": None,
        "overnight_high": None,
        "overnight_low": None,
    }
    try:
        ticker = yf.Ticker("MNQ=F")
        info = getattr(ticker, "fast_info", {}) or {}
        price = (
            info.get("last_price")
            or info.get("lastPrice")
            or info.get("regular_market_price")
        )
        prior_close = info.get("previous_close") or info.get("previousClose")

        hist = ticker.history(period="2d", interval="15m", prepost=True)
        if hist is not None and not hist.empty:
            # Use the latest session worth of bars as "overnight" — for MNQ
            # this captures the post-RTH-close → pre-open globex window.
            session = hist.tail(96)  # 24h of 15-min bars
            result["overnight_high"] = float(session["High"].max())
            result["overnight_low"] = float(session["Low"].min())
            if price is None:
                price = float(session["Close"].iloc[-1])
            if prior_close is None and len(hist) >= 2:
                prior_close = float(hist["Close"].iloc[0])

        result["price"] = float(price) if price else None
        result["prior_close"] = float(prior_close) if prior_close else None
    except Exception as e:
        console.print(f"[red]MNQ premarket fetch failed:[/red] {e}")
    return result


def _gap_label(gap_pct: float) -> str:
    if gap_pct > GAP_THRESHOLD_PCT:
        return "Gap Up"
    if gap_pct < -GAP_THRESHOLD_PCT:
        return "Gap Down"
    return "Flat Open"


def _open_hold_score(
    market: dict, gex: dict, sentiment: dict, regime: dict,
) -> tuple[int, list[str]]:
    """Score in [-3, +4] and the inputs that moved it (for explanation)."""
    score = 0
    notes: list[str] = []

    smh_vs_spy = market.get("smh_vs_spy")
    if smh_vs_spy is not None and smh_vs_spy > 0:
        score += 1
        notes.append(f"SMH +{smh_vs_spy:.2f}% vs SPY")
    if smh_vs_spy is not None and smh_vs_spy < SMH_UNDERPERFORM_PCT:
        score -= 1
        notes.append(f"SMH {smh_vs_spy:.2f}% vs SPY")

    semi_sent = sentiment.get("semi_sentiment_score")
    if semi_sent is not None and semi_sent > 0.2:
        score += 1
        notes.append(f"semi sentiment {semi_sent:+.2f}")

    gex_label = (gex or {}).get("gex_label") or ""
    if gex_label == "suppressing":
        score += 1
        notes.append("GEX suppressing")
    elif gex_label == "amplifying":
        score -= 1
        notes.append("GEX amplifying")

    regime_label = (regime or {}).get("regime_label") or ""
    if regime_label == "Trending Low Vol":
        score += 1
        notes.append("Trending Low Vol")
    elif regime_label == "Mean Reverting":
        score -= 1
        notes.append("Mean Reverting")

    return score, notes


def _open_hold_label(score: int) -> str:
    if score >= 2:
        return "Open likely holds"
    if score >= 0:
        return "Open direction uncertain"
    return "Open likely fades"


def _one_sigma_move(market: dict, mnq_price: float | None) -> float | None:
    """Approximate session 1σ MNQ move from VIX, for sweep-risk comparison."""
    if not mnq_price or mnq_price <= 0:
        return None
    snaps = (market or {}).get("snapshots") or {}
    vix = (snaps.get("^VIX") or {}).get("current_price")
    if not vix or vix <= 0:
        return None
    return (vix / 100.0) / math.sqrt(TRADING_DAYS) * mnq_price


def _gex_magnet(price: float | None, key_level: float | None) -> str | None:
    if price is None or key_level is None or key_level <= 0:
        return None
    if abs(price - key_level) / key_level * 100.0 <= GEX_MAGNET_PCT:
        return (
            f"Price near GEX magnet at {key_level:,.1f} — "
            f"expect gravitational pull"
        )
    return None


def _sweep_risk(
    regime: dict, gex: dict, premarket: dict, sigma_move: float | None,
) -> str | None:
    regime_label = (regime or {}).get("regime_label") or ""
    gex_label = (gex or {}).get("gex_label") or ""
    if regime_label == "Mean Reverting" and gex_label == "amplifying":
        return "High sweep risk — expect liquidity grab before true direction"

    high = premarket.get("overnight_high")
    low = premarket.get("overnight_low")
    if high is not None and low is not None and sigma_move and (high - low) > sigma_move:
        return "Wide overnight range — opening gap fill likely"
    return None


def _open_summary(
    gap_label: str, gap_pct: float, open_hold: str,
    gex_magnet: str | None, sweep_risk: str | None,
) -> str:
    parts = [f"{gap_label} ({gap_pct:+.2f}%)", open_hold]
    if gex_magnet:
        parts.append(gex_magnet)
    if sweep_risk:
        parts.append(sweep_risk)
    return ". ".join(parts) + "."


def compute_open_bias(
    market: dict, options: dict, gex: dict, sentiment: dict, regime: dict,
) -> dict:
    """Forward-looking estimate of the first 30-60 minutes after 9:30 ET.

    Combines the MNQ premarket gap, an open-hold-vs-fade score across semi
    relative strength, sentiment, GEX, and regime, plus magnet/sweep
    callouts. Inputs are all from the 8:45 AM ET snapshot — see
    OPEN_BIAS_DISCLAIMER for the caveat that ships alongside this output.
    """
    market = market or {}
    options = options or {}
    gex = gex or {}
    sentiment = sentiment or {}
    regime = regime or {}

    premarket = _fetch_mnq_premarket()
    price = premarket["price"]
    prior_close = premarket["prior_close"]

    if price is not None and prior_close:
        gap_pct = (price - prior_close) / prior_close * 100.0
    else:
        gap_pct = 0.0
    gap_label = _gap_label(gap_pct)

    score, _notes = _open_hold_score(market, gex, sentiment, regime)
    open_hold = _open_hold_label(score)

    key_level = (gex or {}).get("key_gex_level_mnq")
    gex_magnet = _gex_magnet(price, key_level)

    sigma_move = _one_sigma_move(market, price)
    sweep_risk = _sweep_risk(regime, gex, premarket, sigma_move)

    open_summary = _open_summary(
        gap_label, gap_pct, open_hold, gex_magnet, sweep_risk,
    )

    result = {
        "gap_label": gap_label,
        "gap_pct": round(gap_pct, 3),
        "open_hold": open_hold,
        "open_hold_score": score,
        "gex_magnet": gex_magnet,
        "sweep_risk": sweep_risk,
        "open_summary": open_summary,
        "premarket_price": round(price, 2) if price else None,
        "prior_close": round(prior_close, 2) if prior_close else None,
        "disclaimer": OPEN_BIAS_DISCLAIMER,
    }
    _print_open_bias(result)
    return result


def _print_open_bias(r: dict) -> None:
    color = {"Gap Up": "green", "Gap Down": "red", "Flat Open": "white"}.get(
        r["gap_label"], "white"
    )
    hold_color = {
        "Open likely holds": "green",
        "Open direction uncertain": "yellow",
        "Open likely fades": "red",
    }.get(r["open_hold"], "white")
    console.print(
        f"[bold {color}]Open bias:[/bold {color}] "
        f"{r['gap_label']} ({r['gap_pct']:+.2f}%) — "
        f"[bold {hold_color}]{r['open_hold']}[/bold {hold_color}]"
    )
    if r.get("gex_magnet"):
        console.print(f"  [yellow]{r['gex_magnet']}[/yellow]")
    if r.get("sweep_risk"):
        console.print(f"  [red]{r['sweep_risk']}[/red]")
    console.print(f"  [dim italic]{r['disclaimer']}[/dim italic]")


def _technical_strikes(market: dict, options: dict, semi_health_score: int) -> list[str]:
    """Technical strikes — yield moves, VIX term structure, semi relative strength, IV."""
    market = market or {}
    options = options or {}
    strikes: list[str] = []

    yield_bps = market.get("yield_bps_change") or 0.0
    if abs(yield_bps) > YIELD_BPS_STRIKE:
        strikes.append(f"yield moved {yield_bps:+.1f}bps (>{YIELD_BPS_STRIKE:.0f}bps)")

    if market.get("yield_roc") == "accelerating" and abs(yield_bps) > YIELD_ACCEL_BPS_STRIKE:
        strikes.append(f"yield accelerating at {yield_bps:+.1f}bps")

    if market.get("vix_term_structure") == "backwardation":
        strikes.append("VIX term structure in backwardation")

    smh_vs_spy = market.get("smh_vs_spy")
    if smh_vs_spy is not None and smh_vs_spy < SMH_VS_SPY_STRIKE:
        strikes.append(f"SMH lagging SPY by {smh_vs_spy:+.2f}%")

    smh_vs_qqq = market.get("smh_vs_qqq")
    if smh_vs_qqq is not None and smh_vs_qqq < SMH_VS_QQQ_STRIKE:
        strikes.append(f"SMH lagging QQQ by {smh_vs_qqq:+.2f}%")

    if semi_health_score < SEMI_HEALTH_STRIKE:
        strikes.append(f"semi health weak ({semi_health_score}/100)")

    semi_vs_spy = market.get("semi_vs_spy") or {}
    laggards = [
        s for s, diff in semi_vs_spy.items()
        if diff is not None and diff < SEMI_LAGGARD_PCT
    ]
    if len(laggards) >= SEMI_LAGGARD_COUNT_STRIKE:
        strikes.append(
            f"{len(laggards)} semis lagging SPY >{abs(SEMI_LAGGARD_PCT):.2f}% "
            f"({', '.join(sorted(laggards))})"
        )

    if options.get("nvda_iv_elevated"):
        strikes.append("NVDA IV elevated")

    return strikes


def _sentiment_strikes(sentiment: dict) -> list[str]:
    """Sentiment strikes — FinBERT semi/macro scores and per-ticker guidance cuts."""
    sentiment = sentiment or {}
    strikes: list[str] = []

    semi_score = sentiment.get("semi_sentiment_score")
    if semi_score is not None and semi_score < SEMI_SENTIMENT_STRIKE:
        strikes.append(f"semi sentiment negative ({semi_score:+.2f})")

    macro_score = sentiment.get("macro_sentiment_score")
    if macro_score is not None and macro_score < MACRO_SENTIMENT_STRIKE:
        strikes.append(f"macro sentiment negative ({macro_score:+.2f})")

    guidance_cut = sentiment.get("guidance_cut_flag") or {}
    cut_names = [t for t, flag in guidance_cut.items() if flag]
    if cut_names:
        strikes.append(f"guidance cut flagged on {', '.join(sorted(cut_names))}")

    return strikes


def _event_strikes(sentiment: dict) -> list[str]:
    """One strike if any major macro event (CPI/NFP/FOMC/GDP) is scheduled today."""
    events = (sentiment or {}).get("todays_events") or []
    if not events:
        return []
    names = ", ".join(e.get("matched_term") or e.get("event") or "?" for e in events)
    return [f"major event(s) scheduled today: {names}"]


def _earnings_strikes(sentiment: dict) -> list[str]:
    """Forward earnings strikes — Tier 1 names reporting today or tomorrow BMO.

    Reads the upcoming-earnings list from data.sentiment (next 3 trading days).
    Each condition contributes at most one strike no matter how many names hit.
    NVDA reporting today is handled separately as an automatic RED override.
    """
    upcoming = (sentiment or {}).get("upcoming_earnings") or []
    strikes: list[str] = []

    today_tier1 = [
        e["ticker"] for e in upcoming
        if e.get("tier") == 1 and e.get("is_today")
    ]
    if today_tier1:
        strikes.append(
            f"{'/'.join(today_tier1)} earnings today — elevated uncertainty"
        )

    tomorrow_bmo_tier1 = [
        e["ticker"] for e in upcoming
        if e.get("tier") == 1 and e.get("is_tomorrow")
        and e.get("report_time") == "BMO"
    ]
    if tomorrow_bmo_tier1:
        strikes.append(
            f"{'/'.join(tomorrow_bmo_tier1)} earnings tomorrow before open "
            f"— positioning risk today"
        )

    return strikes


def _auto_red_overrides(
    market: dict, sentiment: dict, regime: dict, semi_health_score: int,
) -> str | None:
    """Conditions that force RED regardless of strike count. Returns the first hit."""
    market = market or {}
    sentiment = sentiment or {}
    regime = regime or {}

    earnings_surprise = sentiment.get("earnings_surprise_pct") or {}
    for ticker in SEMI_NAMES:
        pct = earnings_surprise.get(ticker)
        if pct is not None and abs(pct) > EARNINGS_SURPRISE_RED:
            return f"{ticker} earnings surprise {pct:+.1f}% (>{EARNINGS_SURPRISE_RED:.0f}%)"

    # NVDA reporting today — the single biggest MNQ mover. Treat the edge as
    # broken outright, regardless of the strike count.
    if "NVDA" in (sentiment.get("earnings_today") or []):
        return "NVDA reports earnings today — the single biggest MNQ mover"

    yield_bps = market.get("yield_bps_change") or 0.0
    if market.get("vix_term_structure") == "backwardation" and abs(yield_bps) > YIELD_BPS_STRIKE:
        return f"VIX backwardation with yield {yield_bps:+.1f}bps same day"

    guidance_cut = sentiment.get("guidance_cut_flag") or {}
    cut_names = [t for t in SEMI_NAMES if guidance_cut.get(t)]
    if cut_names and semi_health_score < SEMI_HEALTH_STRIKE:
        return (
            f"guidance cut ({', '.join(sorted(cut_names))}) with "
            f"semi health weak ({semi_health_score}/100)"
        )

    if regime.get("regime_label") == "Chaotic":
        return "regime classified Chaotic"

    return None


def compute_semi_health(market: dict, options: dict, sentiment: dict) -> dict:
    """0-100 semiconductor health score from price action, IV, and earnings.

    `market` is fetch_market_snapshot(); `options` is fetch_options_snapshot();
    `sentiment` is fetch_overnight_sentiment() (source of the earnings inputs).
    """
    market = market or {}
    options = options or {}
    sentiment = sentiment or {}

    # Individual semi names green vs red (40%) — green = vs_spy > 0, 8% each.
    semi_vs_spy = market.get("semi_vs_spy") or {}
    green_names = [s for s in SEMI_NAMES if (semi_vs_spy.get(s) or 0.0) > 0]
    names_score = NAME_WEIGHT * len(green_names)

    # SMH vs QQQ relative strength (25%).
    smh_vs_qqq = market.get("smh_vs_qqq")
    smh_vs_qqq = 0.0 if smh_vs_qqq is None else smh_vs_qqq
    if smh_vs_qqq > 0:
        rs_score = RS_FULL
    elif smh_vs_qqq >= -0.5:
        rs_score = RS_PARTIAL
    else:
        rs_score = 0.0

    # NVDA IV vs elevated flag (20%).
    nvda_elevated = bool(options.get("nvda_iv_elevated"))
    iv_score = 0.0 if nvda_elevated else IV_FULL

    # Recent earnings sentiment (15%, bonuses to 30, penalties stack, floor 0).
    earnings_surprise = sentiment.get("earnings_surprise_pct") or {}
    guidance_cut = sentiment.get("guidance_cut_flag") or {}
    earnings_score = EARNINGS_BASE
    earnings_notes: list[str] = []
    for ticker in SEMI_NAMES:
        surprise = earnings_surprise.get(ticker)
        if surprise is not None:
            if surprise > EARNINGS_SURPRISE_THRESHOLD:
                earnings_score += EARNINGS_BEAT_BONUS
                earnings_notes.append(f"{ticker} beat {surprise:+.1f}%")
            elif surprise < -EARNINGS_SURPRISE_THRESHOLD:
                earnings_score -= EARNINGS_MISS_PENALTY
                earnings_notes.append(f"{ticker} missed {surprise:+.1f}%")
        if guidance_cut.get(ticker):
            earnings_score -= EARNINGS_GUIDANCE_PENALTY
            earnings_notes.append(f"{ticker} cut guidance")
    earnings_score = max(EARNINGS_MIN, min(EARNINGS_MAX, earnings_score))

    raw = names_score + rs_score + iv_score + earnings_score
    semi_health_score = int(round(max(0.0, min(100.0, raw))))

    if semi_health_score >= 70:
        label = "Strong"
    elif semi_health_score >= 40:
        label = "Mixed"
    else:
        label = "Weak"

    reason = _semi_health_reason(
        semi_health_score, label, names_score, green_names,
        rs_score, smh_vs_qqq, iv_score, nvda_elevated,
        earnings_score, earnings_notes,
    )

    result = {
        "semi_health_score": semi_health_score,
        "semi_health_label": label,
        "reason": reason,
    }
    _print_semi_health(result)
    return result


def _semi_health_reason(
    score: int, label: str, names_score: float, green_names: list[str],
    rs_score: float, smh_vs_qqq: float, iv_score: float, nvda_elevated: bool,
    earnings_score: float, earnings_notes: list[str],
) -> str:
    """One sentence citing the component furthest from neutral (its biggest driver)."""
    n_green = len(green_names)
    names_strong = (
        f"{n_green}/5 semis green vs SPY ({', '.join(green_names)})"
        if green_names else "0/5 semis green vs SPY"
    )
    names_weak = f"only {n_green}/5 semis green vs SPY"

    if smh_vs_qqq > 0:
        rs_detail = f"SMH outperforming QQQ by {smh_vs_qqq:.2f}%"
    else:
        rs_detail = f"SMH underperforming QQQ by {abs(smh_vs_qqq):.2f}%"

    iv_detail = "NVDA IV elevated" if nvda_elevated else "NVDA IV not elevated"
    earnings_detail = "; ".join(earnings_notes) if earnings_notes else "no notable recent semi earnings"

    # (score, max, strong_detail, weak_detail) — listed high-weight first so
    # ties in extremity break toward the more material component.
    components = [
        (names_score, 40.0, names_strong, names_weak),
        (earnings_score, 30.0, earnings_detail, earnings_detail),
        (rs_score, 25.0, rs_detail, rs_detail),
        (iv_score, 20.0, iv_detail, iv_detail),
    ]
    c_score, c_max, strong_detail, weak_detail = max(
        components, key=lambda c: abs(c[0] / c[1] - 0.5)
    )
    if c_score / c_max >= 0.5:
        return f"{label} ({score}/100), driven by {strong_detail}."
    return f"{label} ({score}/100), dragged down by {weak_detail}."


def _print_semi_health(r: dict) -> None:
    color = {"Strong": "green", "Mixed": "yellow", "Weak": "red"}[r["semi_health_label"]]
    console.print(
        f"[bold {color}]Semi health: {r['semi_health_label']} "
        f"({r['semi_health_score']}/100)[/bold {color}]"
    )
    console.print(f"  {r['reason']}")


def _verdict(strikes: int) -> str:
    if strikes <= GREEN_MAX:
        return "GREEN"
    if strikes <= YELLOW_MAX:
        return "YELLOW"
    return "RED"


def score(
    market: dict,
    options: dict,
    sentiment: dict,
    regime: dict,
    range_data: dict,
    gex: dict | None = None,
) -> dict:
    """Master scoring engine — combine every input into a final verdict.

    Inputs:
      market     — data.fetcher.fetch_market_snapshot()
      options    — data.options.fetch_options_snapshot()
      sentiment  — data.sentiment.fetch_overnight_sentiment()
      regime     — classifier.regime.classify()
      range_data — classifier.range_model.expected_range()

    Strikes accumulate across technical / sentiment / event categories, the
    regime then nudges the count, and a set of automatic overrides can force
    RED outright. Verdict: 0-1 GREEN, 2 YELLOW, 3+ RED.
    """
    market = market or {}
    options = options or {}
    sentiment = sentiment or {}
    regime = regime or {}
    range_data = range_data or {}
    gex = gex or {}

    # gex_label lives in the gex snapshot — merge it into options so
    # compute_bias() can keep its 4-arg signature.
    bias_options = {**options, "gex_label": gex.get("gex_label") or options.get("gex_label")}
    bias = compute_bias(market, bias_options, sentiment, regime)
    open_bias = compute_open_bias(market, options, gex, sentiment, regime)

    semi_health = compute_semi_health(market, options, sentiment)
    semi_health_score = semi_health["semi_health_score"]
    semi_health_label = semi_health["semi_health_label"]

    technical = _technical_strikes(market, options, semi_health_score)
    sentiment_s = _sentiment_strikes(sentiment)
    events = _event_strikes(sentiment)
    earnings_s = _earnings_strikes(sentiment)
    counted_strikes = technical + sentiment_s + events + earnings_s

    # Regime adjustment is applied after counting (floor 0).
    regime_label = regime.get("regime_label", "Unknown")
    regime_confidence = regime.get("regime_confidence", 0.0)
    strikes_triggered = list(counted_strikes)
    regime_adjustment = 0
    if regime_label == "Mean Reverting":
        regime_adjustment = 1
        strikes_triggered.append("regime Mean Reverting (+1 strike)")
    elif regime_label == "Trending Low Vol":
        regime_adjustment = -1
        strikes_triggered.append("regime Trending Low Vol (-1 strike)")
    strike_count = max(0, len(counted_strikes) + regime_adjustment)

    auto_red_reason = _auto_red_overrides(market, sentiment, regime, semi_health_score)
    auto_red = auto_red_reason is not None

    verdict = "RED" if auto_red else _verdict(strike_count)
    verdict_reason = _verdict_reason(
        verdict, auto_red, auto_red_reason, strike_count,
        counted_strikes, regime_label, semi_health_label,
    )

    result = {
        "verdict": verdict,
        "strike_count": strike_count,
        "strikes_triggered": strikes_triggered,
        "auto_red": auto_red,
        "auto_red_reason": auto_red_reason,
        "regime_label": regime_label,
        "regime_confidence": regime_confidence,
        "semi_health_score": semi_health_score,
        "semi_health_label": semi_health_label,
        "expected_range_low": range_data.get("expected_range_low"),
        "expected_range_high": range_data.get("expected_range_high"),
        "one_sigma_low": range_data.get("one_sigma_low"),
        "one_sigma_high": range_data.get("one_sigma_high"),
        "key_gex_level_mnq": range_data.get("key_gex_level_mnq"),
        "verdict_reason": verdict_reason,
        "bias_score": bias["bias_score"],
        "bias_label": bias["bias_label"],
        "bias_conviction": bias["bias_conviction"],
        "bias_reason": bias["bias_reason"],
        "gap_label": open_bias["gap_label"],
        "gap_pct": open_bias["gap_pct"],
        "open_hold": open_bias["open_hold"],
        "gex_magnet": open_bias["gex_magnet"],
        "sweep_risk": open_bias["sweep_risk"],
        "open_summary": open_bias["open_summary"],
        "open_bias_disclaimer": open_bias["disclaimer"],
    }
    _print_verdict(result)
    return result


def _verdict_reason(
    verdict: str,
    auto_red: bool,
    auto_red_reason: str | None,
    strike_count: int,
    counted_strikes: list[str],
    regime_label: str,
    semi_health_label: str,
) -> str:
    """One plain-English sentence combining the top signals behind the verdict."""
    if auto_red:
        return f"Automatic RED override — {auto_red_reason}."

    if not counted_strikes:
        return (
            f"No strikes — {regime_label} regime with {semi_health_label.lower()} "
            f"semis, the edge is intact, trade the full plan."
        )

    top = "; ".join(counted_strikes[:2])
    if verdict == "GREEN":
        return (
            f"Only {strike_count} strike(s) ({top}) — {regime_label} regime keeps "
            f"the edge intact, trade the full plan."
        )
    if verdict == "YELLOW":
        return (
            f"{strike_count} strikes ({top}) against a {regime_label} regime and "
            f"{semi_health_label.lower()} semis — size down or skip."
        )
    return (
        f"{strike_count} strikes ({top}) against a {regime_label} regime and "
        f"{semi_health_label.lower()} semis — stay out."
    )


def _print_verdict(r: dict) -> None:
    color = {"GREEN": "green", "YELLOW": "yellow", "RED": "red"}[r["verdict"]]
    console.print()
    flag = "  [bold red](AUTO-RED OVERRIDE)[/bold red]" if r["auto_red"] else ""
    console.print(
        f"[bold {color}]{r['verdict']}[/bold {color}] — "
        f"{r['strike_count']} strike(s){flag}"
    )
    console.print(
        f"  Regime: {r['regime_label']} "
        f"[dim]({r['regime_confidence']:.0f}% confidence)[/dim]"
    )
    console.print(
        f"  Semi health: {r['semi_health_label']} ({r['semi_health_score']}/100)"
    )
    if r["strikes_triggered"]:
        console.print("  Strikes triggered:")
        for s in r["strikes_triggered"]:
            console.print(f"    • {s}")
    else:
        console.print("  Strikes triggered: none")
    if r["auto_red_reason"]:
        console.print(f"  [red]Auto-RED:[/red] {r['auto_red_reason']}")
    lo, hi = r["expected_range_low"], r["expected_range_high"]
    if lo is not None and hi is not None:
        console.print(f"  Expected range:  {lo:,.1f} — {hi:,.1f}")
    slo, shi = r["one_sigma_low"], r["one_sigma_high"]
    if slo is not None and shi is not None:
        console.print(f"  1σ range:        {slo:,.1f} — {shi:,.1f}")
    if r["key_gex_level_mnq"] is not None:
        console.print(f"  Key GEX (MNQ ≈): {r['key_gex_level_mnq']:,.1f}")
    bias_color = {
        "Bullish": "green",
        "Lean Bullish": "bright_green",
        "Neutral": "white",
        "Lean Bearish": "yellow",
        "Bearish": "red",
        "No Bias": "grey50",
    }.get(r.get("bias_label", "Neutral"), "white")
    console.print(
        f"  Bias: [bold {bias_color}]{r.get('bias_label', '?').upper()}[/bold {bias_color}] "
        f"({r.get('bias_conviction', '?')}) "
        f"[dim]{r.get('bias_score', 0):+.1f}[/dim]"
    )
    gap_label = r.get("gap_label")
    if gap_label:
        gap_pct = r.get("gap_pct") or 0.0
        open_hold = r.get("open_hold") or "Open direction uncertain"
        gap_color = {"Gap Up": "green", "Gap Down": "red", "Flat Open": "white"}.get(
            gap_label, "white"
        )
        hold_color = {
            "Open likely holds": "green",
            "Open direction uncertain": "yellow",
            "Open likely fades": "red",
        }.get(open_hold, "white")
        console.print(
            f"  Open Bias: [bold {gap_color}]{gap_label}[/bold {gap_color}] "
            f"({gap_pct:+.2f}%) — "
            f"[bold {hold_color}]{open_hold}[/bold {hold_color}]"
        )
        if r.get("gex_magnet"):
            console.print(f"    [yellow]{r['gex_magnet']}[/yellow]")
        if r.get("sweep_risk"):
            console.print(f"    [red]{r['sweep_risk']}[/red]")
        console.print(
            f"    [dim italic]{r.get('open_bias_disclaimer', '')}[/dim italic]"
        )
    console.print(f"  [italic]{r['verdict_reason']}[/italic]")
