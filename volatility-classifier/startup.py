"""Boot-time validation for Vigil.

Two responsibilities, both designed to surface configuration problems in
Railway logs the moment the container starts:

  * validate_env_vars() — fail fast if any required key is missing.
  * check_api_connectivity() — probe every external dependency once and
    return a per-API status dict. Reused by GET /health.

Missing env vars are fatal (we exit cleanly so Railway doesn't retry-loop
on a misconfigured deploy). API reachability failures are non-fatal —
the pipeline already degrades to ESTIMATED / CACHED when an upstream is
down — but they're logged loudly so debugging from Railway logs is one
scroll away.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

import requests
from rich.console import Console

console = Console()

REQUIRED_ENV_VARS = (
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "FINNHUB_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)

OPTIONAL_ENV_VARS = (
    "TRADIER_API_KEY",  # reserved — options data currently sourced from yfinance
    "TZ",
)

_PROBE_TIMEOUT = 5.0


def validate_env_vars() -> None:
    """Exit(1) with a clear log line per missing required env var."""
    missing = [k for k in REQUIRED_ENV_VARS if not os.getenv(k)]
    if missing:
        console.print("[bold red]Startup aborted — required env vars missing:[/bold red]")
        for key in missing:
            console.print(f"  [red]• {key}[/red]")
        console.print(
            "[dim]Set them in the Railway dashboard (Variables tab) "
            "or your local .env, then redeploy.[/dim]"
        )
        sys.exit(1)

    present_optional = [k for k in OPTIONAL_ENV_VARS if os.getenv(k)]
    console.print("[green]Required env vars present.[/green]")
    if present_optional:
        console.print(f"[dim]Optional env vars set: {', '.join(present_optional)}[/dim]")


def _probe(name: str, fn: Callable[[], bool]) -> dict:
    try:
        ok = bool(fn())
        return {"name": name, "ok": ok, "error": None}
    except Exception as e:
        return {"name": name, "ok": False, "error": str(e)[:200]}


def _probe_supabase() -> bool:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return False
    # REST endpoint root — auth header required; HEAD avoids a body roundtrip.
    r = requests.head(
        url.rstrip("/") + "/rest/v1/",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=_PROBE_TIMEOUT,
    )
    return r.status_code < 500


def _probe_finnhub() -> bool:
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        return False
    r = requests.get(
        "https://finnhub.io/api/v1/quote",
        params={"symbol": "AAPL", "token": key},
        timeout=_PROBE_TIMEOUT,
    )
    return r.status_code == 200 and isinstance(r.json(), dict)


def _probe_telegram() -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return False
    r = requests.get(
        f"https://api.telegram.org/bot{token}/getMe",
        timeout=_PROBE_TIMEOUT,
    )
    return r.status_code == 200 and bool(r.json().get("ok"))


def _probe_yfinance() -> bool:
    # yfinance hits query1.finance.yahoo.com; a small history pull validates
    # network egress and that Yahoo isn't rate-limiting this IP.
    import yfinance as yf
    hist = yf.Ticker("^VIX").history(period="1d", interval="1d", prepost=False)
    return not hist.empty


def check_api_connectivity() -> dict:
    """Probe every external API once. Used at boot AND by GET /health."""
    checks = [
        _probe("supabase", _probe_supabase),
        _probe("finnhub", _probe_finnhub),
        _probe("telegram", _probe_telegram),
        _probe("yfinance", _probe_yfinance),
    ]
    all_ok = all(c["ok"] for c in checks)
    return {
        "all_ok": all_ok,
        "checks": {c["name"]: {"ok": c["ok"], "error": c["error"]} for c in checks},
    }


def log_api_connectivity(status: dict) -> None:
    """Pretty-print probe results — runs at boot so Railway logs show them."""
    console.print("[bold]External API connectivity:[/bold]")
    for name, result in status["checks"].items():
        if result["ok"]:
            console.print(f"  [green]✓ {name}[/green]")
        else:
            err = f" — {result['error']}" if result["error"] else ""
            console.print(f"  [red]✗ {name}[/red][dim]{err}[/dim]")
    if not status["all_ok"]:
        console.print(
            "[yellow]Some APIs unreachable — pipeline will fall back to "
            "ESTIMATED/CACHED for those sources.[/yellow]"
        )
