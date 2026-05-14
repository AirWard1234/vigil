from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta

import numpy as np
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from rich.console import Console

console = Console()

ELEVATED_THRESHOLD = 1.20  # current IV > 120% of 20d hist vol → elevated
MIN_DAYS_TO_EXPIRY = 20

SPY_GEX_URL = "https://www.insiderfinance.io/gamma-exposure/SPY"
SCRAPE_DELAY_SECONDS = 3
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_gex_cache: dict | None = None


def _nearest_monthly_expiry(ticker: yf.Ticker) -> str | None:
    """Third-Friday monthly expiry at least MIN_DAYS_TO_EXPIRY out."""
    today = date.today()
    try:
        expirations = list(ticker.options or [])
    except Exception:
        return None
    for exp_str in expirations:
        try:
            d = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (d - today).days >= MIN_DAYS_TO_EXPIRY and d.weekday() == 4 and 15 <= d.day <= 21:
            return exp_str
    return None


def _spot(ticker: yf.Ticker) -> float | None:
    try:
        info = getattr(ticker, "fast_info", {}) or {}
        price = info.get("last_price") or info.get("lastPrice") or info.get("regular_market_price")
        if price:
            return float(price)
        hist = ticker.history(period="2d", interval="1d", prepost=False)
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


def _historical_vol_20d(symbol: str) -> float | None:
    """20 trading-day annualized realized vol as a proxy for average IV."""
    try:
        hist = yf.Ticker(symbol).history(period="35d", interval="1d", prepost=False)
        closes = hist["Close"].dropna().tail(21).values
        if len(closes) < 2:
            return None
        log_returns = np.diff(np.log(closes))
        return float(np.std(log_returns, ddof=1) * np.sqrt(252))
    except Exception:
        return None


def _atm_iv(symbol: str) -> tuple[float | None, float | None]:
    """Return (current_30d_atm_iv, 20d_hist_vol) as decimals (0.30 = 30%)."""
    ticker = yf.Ticker(symbol)
    expiry = _nearest_monthly_expiry(ticker)
    if not expiry:
        return None, None

    spot = _spot(ticker)
    if spot is None:
        return None, None

    try:
        chain = ticker.option_chain(expiry)
    except Exception:
        return None, None

    calls = chain.calls
    puts = chain.puts
    if calls is None or puts is None or calls.empty or puts.empty:
        return None, None

    atm_call = calls.iloc[(calls["strike"] - spot).abs().argsort().iloc[0]]
    atm_put = puts.iloc[(puts["strike"] - spot).abs().argsort().iloc[0]]

    call_iv = float(atm_call.get("impliedVolatility") or 0) or None
    put_iv = float(atm_put.get("impliedVolatility") or 0) or None

    if call_iv is None and put_iv is None:
        return None, _historical_vol_20d(symbol)
    if call_iv is None:
        current_iv = put_iv
    elif put_iv is None:
        current_iv = call_iv
    else:
        current_iv = (call_iv + put_iv) / 2.0

    return current_iv, _historical_vol_20d(symbol)


def fetch_options_snapshot() -> dict:
    result: dict = {
        "smh_iv": None,
        "nvda_iv": None,
        "smh_iv_elevated": False,
        "nvda_iv_elevated": False,
        "source": "YFINANCE",
    }

    try:
        smh_iv, smh_avg = _atm_iv("SMH")
        nvda_iv, nvda_avg = _atm_iv("NVDA")

        result["smh_iv"] = smh_iv
        result["nvda_iv"] = nvda_iv
        if smh_iv is not None and smh_avg:
            result["smh_iv_elevated"] = smh_iv > smh_avg * ELEVATED_THRESHOLD
        if nvda_iv is not None and nvda_avg:
            result["nvda_iv_elevated"] = nvda_iv > nvda_avg * ELEVATED_THRESHOLD
    except Exception as e:
        console.print(f"[red]yfinance options fetch failed:[/red] {e}")
        result["source"] = "ESTIMATED"

    _print_iv_confirmation(result)
    return result


def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:.2f}%" if x is not None else "n/a"


def _print_iv_confirmation(r: dict) -> None:
    console.print(
        f"[bold green]Options snapshot fetched[/bold green] [dim]({r['source']})[/dim]"
    )
    smh_tag = " [red]ELEVATED[/red]" if r["smh_iv_elevated"] else ""
    nvda_tag = " [red]ELEVATED[/red]" if r["nvda_iv_elevated"] else ""
    console.print(f"  SMH 30d ATM IV:  {_fmt_pct(r['smh_iv'])}{smh_tag}")
    console.print(f"  NVDA 30d ATM IV: {_fmt_pct(r['nvda_iv'])}{nvda_tag}")


def _mnq_spot() -> float | None:
    for sym in ("MNQ=F", "NQ=F", "^NDX"):
        try:
            h = yf.Ticker(sym).history(period="2d", interval="1d", prepost=False)
            if not h.empty:
                return float(h["Close"].iloc[-1])
        except Exception:
            continue
    return None


_SUFFIX = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}


def _value_after_label(text: str, label: str) -> float | None:
    """Find first numeric value following `label`, handling $ prefix and T/B/M/K suffix."""
    pat = re.compile(
        rf"{label}\s*[:\-–—]?\s*(-?\$?-?[\d,]+\.?\d*)\s*([TBMK]?)\b",
        re.IGNORECASE,
    )
    m = pat.search(text)
    if not m:
        return None
    raw = m.group(1).replace("$", "").replace(",", "")
    try:
        val = float(raw)
    except ValueError:
        return None
    suffix = m.group(2).upper()
    if suffix in _SUFFIX:
        val *= _SUFFIX[suffix]
    return val


def _empty_gex(label: str = "unavailable") -> dict:
    return {
        "gex_value": None,
        "gex_label": label,
        "key_gex_level_mnq": None,
        "call_wall": None,
        "put_wall": None,
        "zero_gamma_level": None,
    }


def scrape_gex() -> dict:
    """Scrape InsiderFinance SPY GEX page. Cached per session."""
    global _gex_cache
    if _gex_cache is not None:
        return _gex_cache

    time.sleep(SCRAPE_DELAY_SECONDS)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}

    try:
        r = requests.get(SPY_GEX_URL, headers=headers, timeout=15)
        r.raise_for_status()
    except Exception as e:
        console.print(f"[red]InsiderFinance request failed:[/red] {e}")
        _gex_cache = _empty_gex()
        return _gex_cache

    try:
        soup = BeautifulSoup(r.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        net_gex = _value_after_label(page_text, r"Net GEX")
        zero_gamma = (
            _value_after_label(page_text, r"Zero[- ]Gamma\s*Level")
            or _value_after_label(page_text, r"Zero Gamma")
        )
        call_wall = _value_after_label(page_text, r"Call Wall")
        put_wall = _value_after_label(page_text, r"Put Wall")
        peak_gex_strike = (
            _value_after_label(page_text, r"Peak GEX Strike")
            or _value_after_label(page_text, r"Peak Gamma Strike")
            or _value_after_label(page_text, r"Peak GEX")
        )

        if net_gex is None:
            raise ValueError("Could not parse Net GEX from page")

        # "near zero" → use the page-provided Call/Put GEX to estimate gross
        # scale, then call it neutral if |net| is within 10% of that scale.
        call_gex = _value_after_label(page_text, r"Call GEX")
        put_gex = _value_after_label(page_text, r"Put GEX")
        gross = (abs(call_gex) if call_gex else 0) + (abs(put_gex) if put_gex else 0)
        if gross > 0 and abs(net_gex) < 0.10 * gross:
            gex_label = "neutral"
        elif net_gex < 0:
            gex_label = "amplifying"
        elif net_gex > 0:
            gex_label = "suppressing"
        else:
            gex_label = "neutral"

        key_gex_level_mnq = None
        if peak_gex_strike is not None:
            spy_spot = _spot(yf.Ticker("SPY"))
            mnq_price = _mnq_spot()
            if spy_spot and mnq_price and spy_spot > 0:
                key_gex_level_mnq = peak_gex_strike * (mnq_price / spy_spot)

        result = {
            "gex_value": net_gex,
            "gex_label": gex_label,
            "key_gex_level_mnq": key_gex_level_mnq,
            "call_wall": call_wall,
            "put_wall": put_wall,
            "zero_gamma_level": zero_gamma,
        }
    except Exception as e:
        console.print(f"[red]InsiderFinance parse failed:[/red] {e}")
        result = _empty_gex()

    _gex_cache = result
    _print_gex_confirmation(result)
    return result


def fetch_gex_snapshot() -> dict:
    return scrape_gex()


def _print_gex_confirmation(r: dict) -> None:
    if r["gex_value"] is None:
        console.print("[red]GEX snapshot unavailable[/red]")
        return
    interp = {
        "amplifying": "dealers amplify moves (trending)",
        "suppressing": "dealers suppress moves (range)",
        "neutral": "near zero — neutral",
    }.get(r["gex_label"], "")
    console.print("[bold green]GEX snapshot fetched[/bold green] [dim](INSIDERFINANCE)[/dim]")
    console.print(f"  Net GEX:           {r['gex_value']:+,.0f}  [dim]({r['gex_label']} — {interp})[/dim]")
    if r["zero_gamma_level"] is not None:
        console.print(f"  Zero gamma:        {r['zero_gamma_level']:,.2f}")
    if r["call_wall"] is not None:
        console.print(f"  Call wall:         {r['call_wall']:,.2f}")
    if r["put_wall"] is not None:
        console.print(f"  Put wall:          {r['put_wall']:,.2f}")
    if r["key_gex_level_mnq"] is not None:
        console.print(f"  Key level (MNQ ≈): {r['key_gex_level_mnq']:,.1f}")


if __name__ == "__main__":
    fetch_options_snapshot()
    scrape_gex()
