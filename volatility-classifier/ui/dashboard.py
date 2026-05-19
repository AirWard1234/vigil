"""Rich terminal dashboard for the MNQ volatility classifier.

`render()` takes the full set of pipeline snapshots and paints one
black-background board. Text is pure white; green / yellow / red is reserved
for indicators and the verdict only, the way the spec asks for.
"""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from classifier.scorer import YIELD_BPS_STRIKE, YIELD_ACCEL_BPS_STRIKE

ET = ZoneInfo("America/New_York")
console = Console(style="white on black")

GREEN = "bright_green"
RED = "bright_red"
YELLOW = "yellow"
DIM = "grey50"
BG = "white on black"

_VERDICT_COLOR = {"GREEN": GREEN, "YELLOW": YELLOW, "RED": RED}
_SEMI_NAMES = ["NVDA", "AMD", "TSM", "ASML", "INTC"]

_BIAS_COLOR = {
    "Bullish": "green",
    "Lean Bullish": "bright_green",
    "Neutral": "white",
    "Lean Bearish": "yellow",
    "Bearish": "red",
    "No Bias": "grey50",
}


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------

def _missing(value) -> bool:
    if value is None:
        return True
    return isinstance(value, float) and math.isnan(value)


def _num(value, fmt: str = "{:+.2f}", dash: str = "n/a") -> str:
    return dash if _missing(value) else fmt.format(value)


def _signed_color(value) -> str:
    """Green for positive, red for negative, dim for missing/zero."""
    if _missing(value):
        return DIM
    if value > 0:
        return GREEN
    if value < 0:
        return RED
    return "white"


def _dot(value) -> Text:
    return Text("●", style=_signed_color(value))


def _kv_grid() -> Table:
    """Two-column label/value grid sized to fill its panel."""
    t = Table.grid(padding=(0, 1), expand=True)
    t.add_column(justify="left", no_wrap=True)
    t.add_column(justify="right", no_wrap=True)
    return t


def _panel(body, title: str, border: str = DIM) -> Panel:
    return Panel(body, title=title, title_align="left", style=BG, border_style=border)


def _score_bar(score, width: int = 18) -> Text:
    """Centered [-1, 1] bar — red fills left of center, green fills right."""
    bar = Text()
    if _missing(score):
        bar.append("─" * width, style=DIM)
        bar.append("  n/a", style=DIM)
        return bar
    half = width // 2
    filled = int(round(min(1.0, abs(score)) * half))
    if score < 0:
        bar.append("░" * (half - filled), style=DIM)
        bar.append("█" * filled, style=RED)
        bar.append("│", style="white")
        bar.append("░" * half, style=DIM)
    else:
        bar.append("░" * half, style=DIM)
        bar.append("│", style="white")
        bar.append("█" * filled, style=GREEN)
        bar.append("░" * (half - filled), style=DIM)
    color = _signed_color(score)
    bar.append(f"  {score:+.2f}", style=color)
    return bar


# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------

def _header() -> Panel:
    now = datetime.now(ET)
    grid = Table.grid(expand=True)
    grid.add_column(justify="left")
    grid.add_column(justify="right")
    grid.add_row(
        Text("MNQ Volatility Classifier", style="bold white"),
        Text(
            f"{now:%a %b %d, %Y}    {now:%H:%M:%S} ET    "
            f"last updated {now:%H:%M:%S}",
            style=DIM,
        ),
    )
    return _panel(grid, "")


# --------------------------------------------------------------------------
# row 1 — yield / semiconductors / vix complex
# --------------------------------------------------------------------------

def _yield_panel(market: dict) -> Panel:
    bps = market.get("yield_bps_change")
    roc = market.get("yield_roc", "unknown")
    move_strike = not _missing(bps) and abs(bps) > YIELD_BPS_STRIKE
    accel_strike = (
        roc == "accelerating" and not _missing(bps) and abs(bps) > YIELD_ACCEL_BPS_STRIKE
    )
    flagged = move_strike or accel_strike

    t = _kv_grid()
    t.add_row("10Y yield Δ", Text(_num(bps, "{:+.1f} bps"),
                                       style=RED if move_strike else "white"))
    roc_color = RED if accel_strike else (YELLOW if roc == "accelerating" else DIM)
    t.add_row("rate of change", Text(roc, style=roc_color))
    t.add_row(
        "strike flag",
        Text("STRIKE", style=f"bold {RED}") if flagged else Text("clear", style=GREEN),
    )
    return _panel(t, "10Y Yield", border=RED if flagged else DIM)


def _semis_panel(market: dict, verdict: dict) -> Panel:
    semi_vs_spy = market.get("semi_vs_spy") or {}
    smh_spy = market.get("smh_vs_spy")
    smh_qqq = market.get("smh_vs_qqq")
    score = verdict.get("semi_health_score")
    label = verdict.get("semi_health_label", "?")

    t = Table.grid(padding=(0, 1), expand=True)
    t.add_column(width=1)
    t.add_column(justify="left", no_wrap=True)
    t.add_column(justify="right", no_wrap=True)
    t.add_row(_dot(smh_spy), "SMH vs SPY",
              Text(_num(smh_spy, "{:+.2f}%"), style=_signed_color(smh_spy)))
    t.add_row(_dot(smh_qqq), "SMH vs QQQ",
              Text(_num(smh_qqq, "{:+.2f}%"), style=_signed_color(smh_qqq)))
    t.add_row("", Text("─" * 14, style=DIM), "")
    for name in _SEMI_NAMES:
        diff = semi_vs_spy.get(name)
        t.add_row(_dot(diff), name,
                  Text(_num(diff, "{:+.2f}%"), style=_signed_color(diff)))

    health_color = {"Strong": GREEN, "Mixed": YELLOW, "Weak": RED}.get(label, "white")
    health = Text()
    health.append("health  ", style="white")
    health.append(f"{_num(score, '{:d}')}/100  {label}", style=f"bold {health_color}")
    return _panel(Group(t, Text(""), health), "Semiconductors")


def _vix_panel(market: dict) -> Panel:
    snaps = market.get("snapshots") or {}

    def lvl(sym: str):
        return (snaps.get(sym) or {}).get("current_price")

    term = market.get("vix_term_structure", "unknown")
    term_color = {"contango": GREEN, "backwardation": RED,
                  "mixed": YELLOW}.get(term, DIM)
    rv_iv = market.get("realized_vol_vs_vix")

    t = _kv_grid()
    t.add_row("VIX9D", Text(_num(lvl("^VIX9D"), "{:.2f}"), style="white"))
    t.add_row("VIX", Text(_num(lvl("^VIX"), "{:.2f}"), style="white"))
    t.add_row("VIX3M", Text(_num(lvl("^VIX3M"), "{:.2f}"), style="white"))
    t.add_row("term structure", Text(term, style=f"bold {term_color}"))
    t.add_row("realized / implied", Text(_num(rv_iv, "{:.2f}×"), style="white"))
    return _panel(t, "VIX Complex", border=term_color if term != "unknown" else DIM)


# --------------------------------------------------------------------------
# row 2 — options iv / gex / overnight intelligence
# --------------------------------------------------------------------------

def _iv_panel(options: dict) -> Panel:
    source = options.get("source", "?")

    def iv_row(name: str, iv, elevated: bool):
        val = "n/a" if _missing(iv) else f"{iv * 100:.2f}%"
        if elevated:
            return name, Text(f"{val}  ELEVATED", style=f"bold {RED}")
        return name, Text(val, style="white")

    t = _kv_grid()
    t.add_row(*iv_row("SMH 30d ATM IV", options.get("smh_iv"),
                      options.get("smh_iv_elevated", False)))
    t.add_row(*iv_row("NVDA 30d ATM IV", options.get("nvda_iv"),
                      options.get("nvda_iv_elevated", False)))
    t.add_row("", "")
    t.add_row("vs 20d average",
              Text("elevated = >120% of 20d", style=DIM))
    src_color = DIM if source in ("YFINANCE", "FINNHUB") else YELLOW
    return _panel(Group(t, Text(f"source: {source}", style=src_color)), "Options IV")


def _gex_panel(gex: dict) -> Panel:
    value = gex.get("gex_value")
    label = gex.get("gex_label", "unavailable")
    key_level = gex.get("key_gex_level_mnq")
    label_color = {"amplifying": RED, "suppressing": GREEN,
                   "neutral": YELLOW}.get(label, DIM)
    interp = {
        "amplifying": "dealers amplify moves",
        "suppressing": "dealers suppress moves",
        "neutral": "near zero",
    }.get(label, "no data")

    t = _kv_grid()
    t.add_row("net GEX", Text(_num(value, "{:+,.0f}"), style="white"))
    t.add_row("label", Text(label, style=f"bold {label_color}"))
    t.add_row("", Text(interp, style=DIM))
    t.add_row("call wall", Text(_num(gex.get("call_wall"), "{:,.2f}"), style="white"))
    t.add_row("put wall", Text(_num(gex.get("put_wall"), "{:,.2f}"), style="white"))
    t.add_row("zero gamma",
              Text(_num(gex.get("zero_gamma_level"), "{:,.2f}"), style="white"))
    t.add_row("key MNQ level", Text(_num(key_level, "{:,.1f}"),
                                    style=f"bold {YELLOW}"))
    return _panel(t, "GEX", border=label_color if label != "unavailable" else DIM)


def _headline_lines(sentiment: dict) -> Group:
    merged = (sentiment.get("top_3_semi_headlines") or []) + (
        sentiment.get("top_3_macro_headlines") or []
    )
    merged.sort(
        key=lambda h: abs(h.get("sentiment_score", 0.0)) * h.get("weight", 1.0),
        reverse=True,
    )
    color_map = {"positive": GREEN, "negative": RED, "neutral": DIM}
    lines = []
    for h in merged[:3]:
        label = h.get("sentiment", "neutral")
        conf = h.get("confidence", 0.0)
        ticker = h.get("ticker")
        head = (h.get("headline") or "").strip()
        if len(head) > 58:
            head = head[:57] + "…"
        line = Text()
        line.append(f"{label:>8} ", style=color_map.get(label, "white"))
        line.append(f"{conf * 100:3.0f}%  ", style=DIM)
        if ticker:
            line.append(f"{ticker} ", style="white")
        line.append(head, style="white")
        lines.append(line)
    if not lines:
        lines = [Text("no overnight headlines", style=DIM)]
    return Group(*lines)


def _overnight_panel(sentiment: dict) -> Panel:
    semi = sentiment.get("semi_sentiment_score")
    macro = sentiment.get("macro_sentiment_score")

    bars = _kv_grid()
    bars.add_row("semi", _score_bar(semi))
    bars.add_row("macro", _score_bar(macro))
    return _panel(
        Group(bars, Text(""), _headline_lines(sentiment)),
        "Overnight Intelligence",
    )


# --------------------------------------------------------------------------
# row 3 — event risk / expected range
# --------------------------------------------------------------------------

def _events_panel(sentiment: dict) -> Panel:
    events = sentiment.get("todays_events") or []
    earnings = [e for e in (sentiment.get("earnings_data") or []) if e.get("is_semi")]
    guidance = sentiment.get("guidance_cut_flag") or {}

    blocks = []

    ev_table = _kv_grid()
    if events:
        for e in events:
            name = e.get("matched_term") or e.get("event") or "?"
            ev_table.add_row(Text(name, style=f"bold {RED}"),
                             Text(str(e.get("release_time") or "TBD"), style="white"))
    else:
        ev_table.add_row(Text("today's events", style="white"),
                         Text("none scheduled", style=GREEN))
    blocks.append(ev_table)

    blocks.append(Text(""))
    blocks.append(Text("recent semi earnings", style=DIM))
    earn_table = _kv_grid()
    if earnings:
        for e in earnings:
            sym = e.get("symbol", "?")
            surprise = e.get("surprise_pct")
            cut = guidance.get(sym)
            status = "guidance cut" if cut else "guidance ok"
            status_color = RED if cut else GREEN
            row = Text()
            row.append(f"{sym}  ", style="white")
            row.append(_num(surprise, "{:+.1f}% EPS"), style=_signed_color(surprise))
            earn_table.add_row(row, Text(status, style=status_color))
    else:
        earn_table.add_row(Text("none in last 5d", style=DIM), "")
    blocks.append(earn_table)

    return _panel(Group(*blocks), "Event Risk")


def _range_panel(range_data: dict) -> Panel:
    if range_data.get("source") == "ESTIMATED" or "one_sigma_low" not in range_data:
        body = Text("expected range unavailable\nmissing VIX or MNQ price", style=YELLOW)
        return _panel(body, "Expected Range", border=YELLOW)

    per_pt = range_data.get("dollar_value_per_point", 2.0)
    one_lo = range_data.get("one_sigma_low")
    one_hi = range_data.get("one_sigma_high")
    two_lo = range_data.get("two_sigma_low")
    two_hi = range_data.get("two_sigma_high")
    base_move = range_data.get("base_one_sigma_move")
    key_level = range_data.get("key_gex_level_mnq")

    t = _kv_grid()
    t.add_row("MNQ price", Text(_num(range_data.get("mnq_price"), "{:,.2f}"),
                                style="white"))
    t.add_row(
        "1σ range",
        Text(f"{_num(one_lo, '{:,.1f}')} — {_num(one_hi, '{:,.1f}')}", style="white"),
    )
    t.add_row(
        "1σ move",
        Text(f"±{_num(base_move, '{:,.1f}')} pts  "
             f"${_num(range_data.get('one_sigma_dollars'), '{:,.0f}')}", style=DIM),
    )
    t.add_row(
        "2σ range",
        Text(f"{_num(two_lo, '{:,.1f}')} — {_num(two_hi, '{:,.1f}')}", style="white"),
    )
    t.add_row(
        "2σ move",
        Text(f"±{_num(base_move * 2 if not _missing(base_move) else None, '{:,.1f}')} pts  "
             f"${_num(range_data.get('two_sigma_dollars'), '{:,.0f}')}", style=DIM),
    )
    t.add_row(
        "expected (adj)",
        Text(f"{_num(range_data.get('expected_range_low'), '{:,.1f}')} — "
             f"{_num(range_data.get('expected_range_high'), '{:,.1f}')}", style="white"),
    )
    t.add_row("key GEX level", Text(_num(key_level, "{:,.1f}"),
                                    style=f"bold {YELLOW}"))
    return _panel(t, "Expected Range")


# --------------------------------------------------------------------------
# footer — regime + verdict + strikes + reason
# --------------------------------------------------------------------------

def _verdict_panel(verdict: dict, regime: dict) -> Panel:
    v = verdict.get("verdict", "?")
    color = _VERDICT_COLOR.get(v, "white")
    auto_red = verdict.get("auto_red")
    strike_count = verdict.get("strike_count", 0)
    strikes = verdict.get("strikes_triggered") or []

    regime_label = regime.get("regime_label", "Unknown")
    regime_conf = regime.get("regime_confidence", 0.0)
    regime_color = {
        "Trending Low Vol": GREEN,
        "Trending High Vol": YELLOW,
        "Mean Reverting": YELLOW,
        "Chaotic": RED,
    }.get(regime_label, "white")

    regime_line = Text()
    regime_line.append("HMM Regime:  ", style="white")
    regime_line.append(f"{regime_label}", style=f"bold {regime_color}")
    regime_line.append(f"   {regime_conf:.0f}% confidence", style=DIM)

    bias_label = verdict.get("bias_label") or "Neutral"
    bias_conviction = verdict.get("bias_conviction") or "Low"
    bias_reason = verdict.get("bias_reason") or ""
    bias_color = _BIAS_COLOR.get(bias_label, "white")
    bias_line = Text()
    bias_line.append("Bias:        ", style="white")
    bias_line.append(bias_label.upper(), style=f"bold {bias_color}")
    bias_line.append(f" ({bias_conviction})", style=DIM)
    if bias_reason:
        bias_line.append(f" — {bias_reason}", style="white")
    bias_disclaimer = Text(
        "Directional lean only — not a trade signal", style=f"dim italic"
    )

    big = Text(f"  {v}  ", style=f"bold {color} reverse", justify="center")
    flag = ""
    if auto_red:
        flag = "  (AUTO-RED OVERRIDE)"
    count_line = Text(
        f"{strike_count} strike(s){flag}",
        style=f"bold {color}", justify="center",
    )

    if strikes:
        strike_block = Text()
        for s in strikes:
            strike_block.append(f"  • {s}\n", style="white")
    else:
        strike_block = Text("  • no strikes triggered", style=GREEN)

    reason = Text(verdict.get("verdict_reason", ""), style="italic white")

    body = Group(
        regime_line,
        bias_line,
        bias_disclaimer,
        Text(""),
        big,
        count_line,
        Text(""),
        strike_block,
        reason,
    )
    return _panel(body, "Verdict", border=color)


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------

def render(
    market: dict,
    options: dict,
    gex: dict,
    sentiment: dict,
    regime: dict,
    range_data: dict,
    verdict: dict,
) -> None:
    """Paint the full dashboard from the pipeline snapshots."""
    market = market or {}
    options = options or {}
    gex = gex or {}
    sentiment = sentiment or {}
    regime = regime or {}
    range_data = range_data or {}
    verdict = verdict or {}

    layout = Layout()
    layout.split_column(
        Layout(_header(), name="header", size=3),
        Layout(name="row1", ratio=2),
        Layout(name="row2", ratio=2),
        Layout(name="row3", ratio=2),
        Layout(_verdict_panel(verdict, regime), name="footer", size=16),
    )
    layout["row1"].split_row(
        Layout(_yield_panel(market), name="yield"),
        Layout(_semis_panel(market, verdict), name="semis"),
        Layout(_vix_panel(market), name="vix"),
    )
    layout["row2"].split_row(
        Layout(_iv_panel(options), name="iv"),
        Layout(_gex_panel(gex), name="gex"),
        Layout(_overnight_panel(sentiment), name="overnight"),
    )
    layout["row3"].split_row(
        Layout(_events_panel(sentiment), name="events"),
        Layout(_range_panel(range_data), name="range"),
    )

    console.print(layout)
