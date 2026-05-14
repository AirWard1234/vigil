from __future__ import annotations

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

    semi_health = compute_semi_health(market, options, sentiment)
    semi_health_score = semi_health["semi_health_score"]
    semi_health_label = semi_health["semi_health_label"]

    technical = _technical_strikes(market, options, semi_health_score)
    sentiment_s = _sentiment_strikes(sentiment)
    events = _event_strikes(sentiment)
    counted_strikes = technical + sentiment_s + events

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
    console.print(f"  [italic]{r['verdict_reason']}[/italic]")
