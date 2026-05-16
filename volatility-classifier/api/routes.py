"""FastAPI routes for the Vigil pre-market regime classifier.

Endpoints:
  POST /run       — run the full pipeline and upsert today's verdict.
  GET  /latest    — today's verdict, or yesterday's with stale=True.
  GET  /history   — last N daily verdicts (default 30) ordered date desc.
  GET  /health    — status, last_run, today_complete, uptime.
"""

from __future__ import annotations

import math
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from rich.console import Console

from classifier.range_model import expected_range
from classifier.regime import classify as classify_regime
from classifier.scorer import score
from data.fetcher import fetch_market_snapshot
from data.options import fetch_gex_snapshot, fetch_options_snapshot
from data.sentiment import fetch_overnight_sentiment

PKG_ROOT = Path(__file__).resolve().parent.parent
for candidate in (PKG_ROOT / ".env", PKG_ROOT.parent / ".env"):
    if candidate.exists():
        load_dotenv(candidate)
        break

ET = ZoneInfo("America/New_York")
console = Console()
router = APIRouter()

# Server uptime baseline — set when the module is first imported.
_SERVER_START_TS = time.time()


# --------------------------------------------------------------------------
# Supabase client (lazy, so import doesn't fail when keys are absent)
# --------------------------------------------------------------------------

_supabase_client = None


def _supabase():
    """Return a cached Supabase client, or None if not configured."""
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
        console.print(f"[red]Supabase client init failed:[/red] {e}")
        _supabase_client = None
    return _supabase_client


# --------------------------------------------------------------------------
# JSON sanitation — NaN/Inf aren't valid JSON, strip them recursively
# --------------------------------------------------------------------------

def _clean(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


# --------------------------------------------------------------------------
# Pipeline orchestration
# --------------------------------------------------------------------------

def run_pipeline() -> dict:
    """Run the full data → score pipeline and return the assembled result."""
    market = fetch_market_snapshot()
    options = fetch_options_snapshot()
    gex = fetch_gex_snapshot()
    sentiment = fetch_overnight_sentiment()
    regime = classify_regime(market, gex)
    range_forecast = expected_range(market, gex, sentiment, regime)
    verdict = score(market, options, sentiment, regime, range_forecast)

    return {
        "market": market,
        "options": options,
        "gex": gex,
        "sentiment": sentiment,
        "regime": regime,
        "range": range_forecast,
        "verdict": verdict,
    }


# --------------------------------------------------------------------------
# Flatten pipeline result → daily_verdicts row
# --------------------------------------------------------------------------

def _flatten_for_db(result: dict, today: str) -> dict:
    market = result.get("market") or {}
    options = result.get("options") or {}
    gex = result.get("gex") or {}
    sentiment = result.get("sentiment") or {}
    regime = result.get("regime") or {}
    range_data = result.get("range") or {}
    verdict = result.get("verdict") or {}

    guidance_dict = sentiment.get("guidance_cut_flag") or {}
    guidance_any = any(bool(v) for v in guidance_dict.values())

    todays_events = sentiment.get("todays_events") or []
    event_names = [
        e.get("matched_term") or e.get("event") or ""
        for e in todays_events
    ]
    high_impact_event = bool(event_names)

    top_headlines = {
        "semi": sentiment.get("top_3_semi_headlines") or [],
        "macro": sentiment.get("top_3_macro_headlines") or [],
    }

    row = {
        "date": today,
        "verdict": verdict.get("verdict"),
        "strike_count": verdict.get("strike_count"),
        "strikes_triggered": verdict.get("strikes_triggered") or [],
        "regime_label": regime.get("regime_label"),
        "regime_confidence": regime.get("regime_confidence"),

        "yield_bps_change": market.get("yield_bps_change"),
        "yield_accelerating": market.get("yield_roc") == "accelerating",
        "smh_vs_spy": market.get("smh_vs_spy"),
        "smh_vs_qqq": market.get("smh_vs_qqq"),
        "semi_health_score": verdict.get("semi_health_score"),
        "semi_health_label": verdict.get("semi_health_label"),
        "vix_term_structure": market.get("vix_term_structure"),
        "realized_vs_implied": market.get("realized_vol_vs_vix"),

        "smh_iv": options.get("smh_iv"),
        "nvda_iv": options.get("nvda_iv"),
        "smh_iv_elevated": bool(options.get("smh_iv_elevated")),
        "nvda_iv_elevated": bool(options.get("nvda_iv_elevated")),

        "gex_value": gex.get("gex_value"),
        "gex_label": gex.get("gex_label"),
        "gex_key_level_mnq": gex.get("key_gex_level_mnq"),

        "semi_sentiment_score": sentiment.get("semi_sentiment_score"),
        "macro_sentiment_score": sentiment.get("macro_sentiment_score"),
        "top_headlines": top_headlines,

        "high_impact_event_today": high_impact_event,
        "event_names": event_names,
        "earnings_flag": bool(sentiment.get("earnings_data")),
        "guidance_cut_flag": guidance_any,

        "expected_range_low": range_data.get("expected_range_low"),
        "expected_range_high": range_data.get("expected_range_high"),
        "one_sigma_low": range_data.get("one_sigma_low"),
        "one_sigma_high": range_data.get("one_sigma_high"),

        "verdict_reason": verdict.get("verdict_reason"),
    }
    return _clean(row)


def _today_et() -> str:
    return datetime.now(ET).date().isoformat()


def _persist(row: dict) -> None:
    """Upsert today's verdict — overwrite if it already exists."""
    client = _supabase()
    if client is None:
        console.print("[yellow]Supabase not configured — skipping persistence[/yellow]")
        return
    try:
        client.table("daily_verdicts").upsert(row, on_conflict="date").execute()
    except Exception as e:
        console.print(f"[red]Supabase upsert failed:[/red] {e}")


def run_pipeline_and_persist() -> dict:
    """Run the pipeline, persist today's verdict, and return the result."""
    result = run_pipeline()
    row = _flatten_for_db(result, _today_et())
    _persist(row)
    return _clean(result)


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@router.post("/run")
def post_run() -> dict:
    """Trigger the full pipeline and overwrite today's verdict in Supabase."""
    try:
        return run_pipeline_and_persist()
    except Exception as e:
        console.print(f"[red]Pipeline run failed:[/red] {e}")
        raise HTTPException(status_code=500, detail=f"pipeline failed: {e}")


@router.get("/latest")
def get_latest() -> dict:
    """Return today's verdict, or yesterday's tagged stale=True if not yet run."""
    client = _supabase()
    if client is None:
        raise HTTPException(status_code=503, detail="supabase not configured")

    today = datetime.now(ET).date()
    today_iso = today.isoformat()
    yesterday_iso = (today - timedelta(days=1)).isoformat()

    try:
        resp = (
            client.table("daily_verdicts")
            .select("*")
            .eq("date", today_iso)
            .limit(1)
            .execute()
        )
        if resp.data:
            return _clean(resp.data[0])

        resp = (
            client.table("daily_verdicts")
            .select("*")
            .lte("date", yesterday_iso)
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            row = _clean(resp.data[0])
            row["stale"] = True
            return row

        raise HTTPException(status_code=404, detail="no verdicts found")
    except HTTPException:
        raise
    except Exception as e:
        console.print(f"[red]Latest fetch failed:[/red] {e}")
        raise HTTPException(status_code=500, detail=f"latest failed: {e}")


@router.get("/history")
def get_history(days: int = Query(default=30, ge=1, le=365)) -> dict:
    """Return the last N daily verdicts, newest first."""
    client = _supabase()
    if client is None:
        raise HTTPException(status_code=503, detail="supabase not configured")

    try:
        resp = (
            client.table("daily_verdicts")
            .select("*")
            .order("date", desc=True)
            .limit(days)
            .execute()
        )
        return {
            "count": len(resp.data or []),
            "days": days,
            "verdicts": _clean(resp.data or []),
        }
    except Exception as e:
        console.print(f"[red]History fetch failed:[/red] {e}")
        raise HTTPException(status_code=500, detail=f"history failed: {e}")


@router.post("/telegram/webhook")
def telegram_webhook(update: dict) -> dict:
    """Receive Telegram bot updates and dispatch /verdict and /range commands."""
    from alerts.telegram import handle_telegram_update
    try:
        handle_telegram_update(update)
    except Exception as e:
        console.print(f"[red]Telegram webhook handler failed:[/red] {e}")
    return {"ok": True}


@router.get("/health")
def get_health() -> dict:
    """Liveness + per-API connectivity + state of today's pipeline run.

    Returns 200 even when upstream APIs are down — the service itself is
    still alive and the dependencies block tells you which ones aren't.
    Railway's healthcheck just needs a 2xx; the body is for humans
    debugging from logs.
    """
    from startup import check_api_connectivity

    uptime = time.time() - _SERVER_START_TS
    client = _supabase()
    last_run: str | None = None
    today_complete = False

    if client is not None:
        try:
            resp = (
                client.table("daily_verdicts")
                .select("date, created_at")
                .order("date", desc=True)
                .limit(1)
                .execute()
            )
            if resp.data:
                last_row = resp.data[0]
                last_run = last_row.get("created_at") or last_row.get("date")
                today_complete = last_row.get("date") == _today_et()
        except Exception as e:
            console.print(f"[red]Health Supabase check failed:[/red] {e}")

    connectivity = check_api_connectivity()

    return {
        "status": "ok",
        "last_run": last_run,
        "today_complete": today_complete,
        "uptime": round(uptime, 2),
        "dependencies": connectivity["checks"],
        "all_dependencies_ok": connectivity["all_ok"],
    }
