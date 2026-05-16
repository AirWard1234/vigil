"""Telegram alerts for Vigil.

Two scheduled jobs (registered by main.py):
  * send_morning_alert_job()  — Mon-Fri 8:47 AM ET. Reads today's row from
    Supabase (just persisted by the 8:45 pipeline run) and posts the
    morning verdict message.
  * check_vix_intraday()      — Mon-Fri every 15 mins between 9:30 and
    16:00 ET. Captures the 9:30 ^VIX open, then alerts once if VIX moves
    more than VIX_SPIKE_THRESHOLD_PCT in either direction.

Plus a webhook handler for two bot commands, dispatched from
api/routes.py POST /telegram/webhook:
  /verdict  — returns the latest verdict
  /range    — returns today's expected MNQ range

Telegram credentials live in .env as TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time as dtime
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from dotenv import load_dotenv
from rich.console import Console

# Load .env at module import — mirrors api/routes.py so this module works
# both under uvicorn AND when invoked standalone (e.g. `python -c` or tests).
PKG_ROOT = Path(__file__).resolve().parent.parent
for _candidate in (PKG_ROOT / ".env", PKG_ROOT.parent / ".env"):
    if _candidate.exists():
        load_dotenv(_candidate)
        break

console = Console()
ET = ZoneInfo("America/New_York")

VIX_SPIKE_THRESHOLD_PCT = 15.0
INTRADAY_START = dtime(9, 30)
INTRADAY_END = dtime(16, 0)

# Approach copy keyed (verdict, regime); _GENERIC_APPROACH is the fallback.
_APPROACH = {
    ("GREEN", "Trending Low Vol"): "trend following, hold runners",
    ("GREEN", "Trending High Vol"): "trade trend, wider stops",
    ("GREEN", "Mean Reverting"): "fade extremes, scalp the range",
}
_GENERIC_APPROACH = {
    "GREEN": "trade the full plan",
    "YELLOW": "size down or skip — edge is degraded",
    "RED": "stay out — edge is broken",
}

_VERDICT_EMOJI = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}

# Translate the internal GEX label into the morning-alert phrasing.
_GEX_DESCRIPTOR = {
    "amplifying": ("Negative", "moves amplify"),
    "suppressing": ("Positive", "moves suppressed"),
    "neutral": ("Neutral", "no skew"),
    "unavailable": ("Unavailable", ""),
}

# Intraday VIX state — reset on the first run of each new day.
_state_lock = Lock()
_today_open_vix: float | None = None
_open_vix_date: date | None = None
_regime_alert_sent_date: date | None = None


# --------------------------------------------------------------------------
# Low-level Telegram HTTP
# --------------------------------------------------------------------------

def _bot_token() -> str | None:
    return os.getenv("TELEGRAM_BOT_TOKEN") or None


def _default_chat_id() -> str | None:
    return os.getenv("TELEGRAM_CHAT_ID") or None


def send_alert(message: str, chat_id: str | int | None = None) -> None:
    """Send a free-form message to Telegram. Defaults to TELEGRAM_CHAT_ID."""
    token = _bot_token()
    target = chat_id if chat_id is not None else _default_chat_id()
    if not token or not target:
        console.print("[yellow]Telegram not configured — skipping alert[/yellow]")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": target, "text": message},
            timeout=10,
        )
    except Exception as e:
        console.print(f"[red]Telegram sendMessage failed:[/red] {e}")


# --------------------------------------------------------------------------
# Supabase access (self-contained — avoids importing api/routes)
# --------------------------------------------------------------------------

_supabase_client = None


def _supabase():
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _supabase_client = create_client(url, key)
    except Exception as e:
        console.print(f"[red]Supabase init failed in alerts:[/red] {e}")
        _supabase_client = None
    return _supabase_client


def _load_latest_row() -> dict | None:
    """Return today's verdict row, falling back to most recent if missing."""
    client = _supabase()
    if client is None:
        console.print("[yellow]Supabase not configured — check SUPABASE_URL/SUPABASE_KEY[/yellow]")
        return None
    today = datetime.now(ET).date().isoformat()
    try:
        console.print(f"[dim]Querying daily_verdicts where date='{today}'[/dim]")
        resp = (
            client.table("daily_verdicts")
            .select("*")
            .eq("date", today)
            .limit(1)
            .execute()
        )
        if resp.data:
            console.print(f"[dim]Found row for {today}: verdict={resp.data[0].get('verdict')}[/dim]")
            return resp.data[0]
        console.print(f"[yellow]No row for {today}, falling back to most recent[/yellow]")
        resp = (
            client.table("daily_verdicts")
            .select("*")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            row = dict(resp.data[0])
            row["stale"] = True
            console.print(f"[dim]Latest row: date={row.get('date')} verdict={row.get('verdict')}[/dim]")
            return row
        console.print("[yellow]daily_verdicts table appears empty[/yellow]")
    except Exception as e:
        console.print(f"[red]Supabase verdict fetch failed:[/red] {e}")
    return None


# --------------------------------------------------------------------------
# Message formatting
# --------------------------------------------------------------------------

def _fmt_yield(bps: float | None, accelerating: bool | None) -> str:
    if bps is None:
        return "Yield: n/a"
    state = "accelerating" if accelerating else "stable"
    return f"Yield: {bps:+.0f}bps {state}"


def _fmt_range(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "Range: n/a"
    return f"Range: {low:,.0f} — {high:,.0f} (1σ)"


def _fmt_gex(label: str | None) -> str:
    name, desc = _GEX_DESCRIPTOR.get(label or "unavailable", ("Unknown", ""))
    return f"GEX: {name} — {desc}" if desc else f"GEX: {name}"


def _fmt_semis(label: str | None, score: int | None) -> str:
    if label is None or score is None:
        return "Semis: n/a"
    return f"Semis: {label} ({score}/100)"


def _fmt_event_risk(has_event: bool | None, names: list[str] | None) -> str:
    if has_event and names:
        return f"Event risk: {', '.join(n for n in names if n)}"
    return "No event risk"


def _approach_line(verdict: str, regime: str | None) -> str:
    return (
        _APPROACH.get((verdict, regime or ""))
        or _GENERIC_APPROACH.get(verdict, "trade the plan")
    )


def format_morning_alert(row: dict) -> str:
    """Compose the morning Telegram message from a flattened verdict row."""
    verdict = (row.get("verdict") or "UNKNOWN").upper()
    emoji = _VERDICT_EMOJI.get(verdict, "⚪")
    regime = row.get("regime_label") or "Unknown"
    confidence = row.get("regime_confidence")
    conf_str = f"{int(round(confidence))}%" if confidence is not None else "n/a"

    lines = [
        f"{emoji} {verdict} — {regime} ({conf_str})",
        _fmt_range(row.get("one_sigma_low"), row.get("one_sigma_high")),
        _fmt_gex(row.get("gex_label")),
        _fmt_semis(row.get("semi_health_label"), row.get("semi_health_score")),
        _fmt_yield(row.get("yield_bps_change"), row.get("yield_accelerating")),
        _fmt_event_risk(row.get("high_impact_event_today"), row.get("event_names")),
        f"Approach: {_approach_line(verdict, regime)}",
    ]
    if row.get("stale"):
        lines.append("(no run today yet — showing latest)")
    return "\n".join(lines)


def format_range_message(row: dict) -> str:
    """Just the expected range info — for /range command."""
    parts = []
    e_lo, e_hi = row.get("expected_range_low"), row.get("expected_range_high")
    s_lo, s_hi = row.get("one_sigma_low"), row.get("one_sigma_high")
    gex_key = row.get("gex_key_level_mnq")

    if e_lo is not None and e_hi is not None:
        parts.append(f"Expected range: {e_lo:,.0f} — {e_hi:,.0f}")
    if s_lo is not None and s_hi is not None:
        parts.append(f"1σ band: {s_lo:,.0f} — {s_hi:,.0f}")
    if gex_key is not None:
        parts.append(f"Key GEX (MNQ ≈): {gex_key:,.0f}")
    if row.get("stale"):
        parts.append("(no run today yet — showing latest)")
    return "\n".join(parts) if parts else "Range data not available."


# --------------------------------------------------------------------------
# Scheduled jobs
# --------------------------------------------------------------------------

def send_morning_alert_job() -> None:
    """8:47 AM ET hook — fetch today's row and send the morning verdict."""
    row = _load_latest_row()
    if not row:
        console.print("[yellow]No verdict row available for morning alert[/yellow]")
        return
    send_alert(format_morning_alert(row))
    console.print("[green]Morning Telegram alert sent[/green]")


def _fetch_vix() -> float | None:
    try:
        info = getattr(yf.Ticker("^VIX"), "fast_info", {}) or {}
        price = (
            info.get("last_price")
            or info.get("lastPrice")
            or info.get("regular_market_price")
        )
        if price:
            return float(price)
        hist = yf.Ticker("^VIX").history(period="1d", interval="1m", prepost=False)
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        console.print(f"[red]VIX intraday fetch failed:[/red] {e}")
    return None


def check_vix_intraday() -> None:
    """Every 15 mins between 9:30 and 16:00 ET — alert on >15% VIX moves."""
    global _today_open_vix, _open_vix_date, _regime_alert_sent_date
    now_et = datetime.now(ET)
    if not (INTRADAY_START <= now_et.time() <= INTRADAY_END):
        return

    vix = _fetch_vix()
    if vix is None:
        return

    today = now_et.date()
    with _state_lock:
        if _open_vix_date != today:
            _today_open_vix = vix
            _open_vix_date = today
            _regime_alert_sent_date = None
            console.print(f"[dim]VIX open captured: {vix:.2f}[/dim]")
            return

        if _today_open_vix is None or _today_open_vix <= 0:
            return
        if _regime_alert_sent_date == today:
            return

        pct_move = (vix - _today_open_vix) / _today_open_vix * 100.0
        if abs(pct_move) <= VIX_SPIKE_THRESHOLD_PCT:
            return

        _regime_alert_sent_date = today

    send_alert(
        f"⚠️ REGIME SHIFT — VIX spiked {pct_move:+.1f}%\n"
        f"Conditions changed. Reassess open positions."
    )
    console.print(f"[red]VIX regime-shift alert sent ({pct_move:+.1f}%)[/red]")


# --------------------------------------------------------------------------
# Webhook handler — /start, /verdict, /range
# --------------------------------------------------------------------------

WELCOME_MESSAGE = (
    "👋 Welcome to Vigil.\n\n"
    "I'm your pre-market MNQ regime classifier.\n\n"
    "Every morning at 8:45 AM ET I'll send you a verdict "
    "before the market opens.\n\n"
    "Commands:\n"
    "/verdict — get today's verdict\n"
    "/range — get today's expected MNQ range\n\n"
    "Stay disciplined."
)


def handle_telegram_update(update: dict) -> None:
    """Dispatch incoming Telegram updates to /start, /verdict, /range handlers."""
    msg = (update or {}).get("message") or (update or {}).get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not text or chat_id is None:
        return

    cmd = text.split()[0].split("@")[0].lower()

    # /start is open to anyone — handled before the authz check so a new
    # user can discover the bot and see what it does.
    if cmd == "/start":
        send_alert(WELCOME_MESSAGE, chat_id=chat_id)
        return

    # Everything below requires the configured chat ID.
    allowed = _default_chat_id()
    if allowed and str(chat_id) != str(allowed):
        return

    if cmd == "/verdict":
        row = _load_latest_row()
        reply = format_morning_alert(row) if row else "No verdict available yet."
    elif cmd == "/range":
        row = _load_latest_row()
        reply = format_range_message(row) if row else "No range available yet."
    else:
        return

    send_alert(reply, chat_id=chat_id)
