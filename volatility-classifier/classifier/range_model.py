"""Forward-looking session expected-range model for MNQ.

Base 1σ daily move = (VIX / 100 / sqrt(252)) × MNQ price. VIX is quoted in
percentage points, so it is divided by 100 to become a decimal annualized vol
before being scaled to one trading day.

Two layers of output:
  * one_sigma / two_sigma — the pure VIX-implied 68% / 95% statistical bands.
  * expected_range — the base 1σ move stacked with today's risk multipliers
    (sentiment, yield, earnings, events, GEX, regime). This is Vigil's
    risk-adjusted forecast and is usually wider than the raw 1σ band.
"""

from __future__ import annotations

import math

import yfinance as yf
from rich.console import Console

console = Console()

TRADING_DAYS = 252
DOLLAR_VALUE_PER_POINT = 2.0  # MNQ — $2 per index point

SEMI_SENTIMENT_THRESHOLD = -0.3
SEMI_SENTIMENT_MULT = 1.15
YIELD_BPS_THRESHOLD = 5.0
YIELD_BPS_MULT = 1.10
YIELD_ACCEL_MULT = 1.05
EARNINGS_MULT = 1.20
EVENT_MULT = 1.25
GEX_AMPLIFYING_MULT = 1.10
GEX_SUPPRESSING_MULT = 0.90
REGIME_TRENDING_LOW_VOL_MULT = 0.95
REGIME_CHAOTIC_MULT = 1.30

_MNQ_TICKERS = ("MNQ=F", "NQ=F", "^NDX")


def _mnq_price() -> float | None:
    """Latest MNQ close, falling back through NQ=F then ^NDX."""
    for sym in _MNQ_TICKERS:
        try:
            hist = yf.Ticker(sym).history(period="2d", interval="1d", prepost=False)
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            continue
    return None


def _multipliers(
    market: dict, gex: dict, sentiment: dict, regime_label: str,
) -> list[tuple[str, float]]:
    """Risk multipliers that apply to today's session, in (reason, factor) form."""
    applied: list[tuple[str, float]] = []

    semi_score = sentiment.get("semi_sentiment_score", 0.0) or 0.0
    if semi_score < SEMI_SENTIMENT_THRESHOLD:
        applied.append((f"semi sentiment {semi_score:+.2f}", SEMI_SENTIMENT_MULT))

    yield_bps = market.get("yield_bps_change", 0.0) or 0.0
    if abs(yield_bps) > YIELD_BPS_THRESHOLD:
        applied.append((f"yield move {yield_bps:+.1f}bps", YIELD_BPS_MULT))

    if market.get("yield_roc") == "accelerating":
        applied.append(("yield accelerating", YIELD_ACCEL_MULT))

    if any(e.get("is_semi") for e in sentiment.get("earnings_data") or []):
        applied.append(("recent semi earnings", EARNINGS_MULT))

    events = sentiment.get("todays_events") or []
    if events:
        names = ", ".join(e.get("matched_term") or e.get("event") or "?" for e in events)
        applied.append((f"high-impact event ({names})", EVENT_MULT))

    gex_label = (gex or {}).get("gex_label")
    if gex_label == "amplifying":
        applied.append(("GEX amplifying", GEX_AMPLIFYING_MULT))
    elif gex_label == "suppressing":
        applied.append(("GEX suppressing", GEX_SUPPRESSING_MULT))

    if regime_label == "Trending Low Vol":
        applied.append(("regime Trending Low Vol", REGIME_TRENDING_LOW_VOL_MULT))
    elif regime_label == "Chaotic":
        applied.append(("regime Chaotic", REGIME_CHAOTIC_MULT))

    return applied


def expected_range(market: dict, gex: dict, sentiment: dict, regime: dict) -> dict:
    """Compute the session expected range for MNQ.

    `market` is data.fetcher.fetch_market_snapshot(); `gex` is
    data.options.fetch_gex_snapshot() (also the source of key_gex_level_mnq);
    `sentiment` is data.sentiment.fetch_overnight_sentiment(); `regime` is
    classifier.regime.classify().
    """
    vix = market.get("snapshots", {}).get("^VIX", {}).get("current_price")
    mnq_price = _mnq_price()
    regime_label = (regime or {}).get("regime_label", "")

    if not vix or not mnq_price or vix <= 0 or mnq_price <= 0:
        console.print("[red]Expected range unavailable — missing VIX or MNQ price[/red]")
        return {"mnq_price": mnq_price, "vix": vix, "source": "ESTIMATED"}

    base_move = (vix / 100.0) / math.sqrt(TRADING_DAYS) * mnq_price

    multipliers = _multipliers(market, gex, sentiment, regime_label)
    stacked = 1.0
    for _, factor in multipliers:
        stacked *= factor
    adjusted_move = base_move * stacked

    midpoint = mnq_price
    result = {
        "mnq_price": round(mnq_price, 2),
        "vix": round(vix, 2),
        "base_one_sigma_move": round(base_move, 2),
        "adjusted_move": round(adjusted_move, 2),
        "multiplier_total": round(stacked, 4),
        "multipliers_applied": [f"{name} ×{factor}" for name, factor in multipliers],

        "expected_range_low": round(midpoint - adjusted_move, 2),
        "expected_range_high": round(midpoint + adjusted_move, 2),

        "one_sigma_low": round(midpoint - base_move, 2),
        "one_sigma_high": round(midpoint + base_move, 2),
        "two_sigma_low": round(midpoint - 2 * base_move, 2),
        "two_sigma_high": round(midpoint + 2 * base_move, 2),

        "key_gex_level_mnq": (gex or {}).get("key_gex_level_mnq"),

        "dollar_value_per_point": DOLLAR_VALUE_PER_POINT,
        "one_sigma_dollars": round(base_move * DOLLAR_VALUE_PER_POINT, 2),
        "two_sigma_dollars": round(2 * base_move * DOLLAR_VALUE_PER_POINT, 2),

        "source": "YFINANCE",
    }
    _print_confirmation(result)
    return result


def _print_confirmation(r: dict) -> None:
    console.print(
        f"[bold green]Expected range computed[/bold green] [dim]({r['source']})[/dim]"
    )
    console.print(f"  MNQ price:        {r['mnq_price']:,.2f}   VIX: {r['vix']:.2f}")
    console.print(f"  Base 1σ move:     {r['base_one_sigma_move']:,.2f} pts")
    if r["multipliers_applied"]:
        console.print(
            f"  Multipliers:      {r['multiplier_total']:.3f}×  "
            f"[dim]({'; '.join(r['multipliers_applied'])})[/dim]"
        )
    else:
        console.print("  Multipliers:      1.000×  [dim](none applied)[/dim]")
    console.print(f"  Adjusted move:    {r['adjusted_move']:,.2f} pts")
    console.print(
        f"  Expected range:   {r['expected_range_low']:,.1f} — {r['expected_range_high']:,.1f}"
    )
    console.print(
        f"  1σ band (68%):    {r['one_sigma_low']:,.1f} — {r['one_sigma_high']:,.1f}   "
        f"[dim](${r['one_sigma_dollars']:,.0f})[/dim]"
    )
    console.print(
        f"  2σ band (95%):    {r['two_sigma_low']:,.1f} — {r['two_sigma_high']:,.1f}   "
        f"[dim](${r['two_sigma_dollars']:,.0f})[/dim]"
    )
    if r.get("key_gex_level_mnq") is not None:
        console.print(f"  Key GEX (MNQ ≈):  {r['key_gex_level_mnq']:,.1f}")
