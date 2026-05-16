"""Vigil entry point.

- `uvicorn main:app` (production / Procfile): serves the FastAPI app and runs
  APScheduler — HMM retrains Mondays 8:00 AM ET, full pipeline runs weekdays
  8:45 AM ET.
- `python main.py`: runs the pipeline once and renders the rich terminal
  dashboard (no server, no scheduler).
- `python main.py --live`: refreshes the dashboard every 60s.
- `python main.py --test`: passes through to the pipeline modules so callers
  can swap in mock data without hitting market hours.
"""

from __future__ import annotations

import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from fastapi import FastAPI
from rich.console import Console
from rich.panel import Panel

for candidate in (Path(__file__).resolve().parent / ".env",
                  Path(__file__).resolve().parent.parent / ".env"):
    if candidate.exists():
        load_dotenv(candidate)
        break

from alerts.telegram import check_vix_intraday, send_morning_alert_job
from api.routes import router, run_pipeline, run_pipeline_and_persist
from classifier.regime import retrain as retrain_regime
from startup import (
    check_api_connectivity,
    log_api_connectivity,
    validate_env_vars,
)
from ui.dashboard import render

console = Console()
_scheduler: BackgroundScheduler | None = None


def _start_scheduler() -> BackgroundScheduler:
    """Register the weekday 8:45 ET pipeline run + Monday 8:00 ET HMM retrain."""
    scheduler = BackgroundScheduler(timezone="America/New_York")

    scheduler.add_job(
        retrain_regime,
        CronTrigger(day_of_week="mon", hour=8, minute=0),
        id="regime_retrain",
        replace_existing=True,
    )
    scheduler.add_job(
        run_pipeline_and_persist,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=45),
        id="daily_pipeline",
        replace_existing=True,
    )
    scheduler.add_job(
        send_morning_alert_job,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=47),
        id="morning_alert",
        replace_existing=True,
    )
    # 15-min cadence Mon-Fri; the job itself filters to the 9:30–16:00 window.
    scheduler.add_job(
        check_vix_intraday,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="0,15,30,45"),
        id="vix_intraday",
        replace_existing=True,
    )

    scheduler.start()
    console.print(
        "[green]APScheduler started[/green] [dim](Mon 8:00 retrain, "
        "Mon-Fri 8:45 run, 8:47 alert, 9:30-16:00 VIX watch)[/dim]"
    )
    return scheduler


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _scheduler
    validate_env_vars()
    log_api_connectivity(check_api_connectivity())
    _scheduler = _start_scheduler()
    try:
        yield
    finally:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)


app = FastAPI(title="Vigil — MNQ Pre-Market Regime Classifier", lifespan=_lifespan)
app.include_router(router)


def _render_once() -> dict:
    result = run_pipeline()
    render(
        result["market"],
        result["options"],
        result["gex"],
        result["sentiment"],
        result["regime"],
        result["range"],
        result["verdict"],
    )
    return result


def main() -> None:
    console.print(
        Panel.fit(
            "[bold green]Vigil — Volatility Classifier[/bold green]\n"
            "[dim]Pre-market regime intelligence for MNQ[/dim]",
            border_style="green",
        )
    )

    live = "--live" in sys.argv

    if live:
        try:
            while True:
                _render_once()
                console.print("[dim]Refreshing in 60s…  Ctrl-C to stop.[/dim]")
                time.sleep(60)
        except KeyboardInterrupt:
            console.print("\n[yellow]Live mode stopped.[/yellow]")
    else:
        _render_once()


if __name__ == "__main__":
    main()
