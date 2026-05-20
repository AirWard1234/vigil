from __future__ import annotations

import numpy as np
import yfinance as yf
from rich.console import Console

console = Console()

MACRO = ["^TNX", "^VIX", "^VIX9D", "^VIX3M"]
SEMIS = ["SMH", "QQQ", "SPY", "NVDA", "AMD", "TSM", "ASML", "INTC"]
UNDERPERFORM_THRESHOLD_PCT = 0.75


def _ticker_snapshot(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    hist = t.history(period="10d", interval="1d", prepost=False)
    closes = hist["Close"].dropna().tail(5).tolist()

    info = getattr(t, "fast_info", {}) or {}
    prev_close = (
        info.get("previous_close")
        or info.get("previousClose")
        or (closes[-2] if len(closes) >= 2 else (closes[-1] if closes else float("nan")))
    )
    last_price = (
        info.get("last_price")
        or info.get("lastPrice")
        or info.get("regular_market_price")
        or (closes[-1] if closes else prev_close)
    )
    pct_change = (
        ((last_price - prev_close) / prev_close * 100.0)
        if prev_close and not np.isnan(prev_close)
        else float("nan")
    )

    return {
        "ticker": ticker,
        "previous_close": float(prev_close),
        "current_price": float(last_price) if last_price else float("nan"),
        "pct_change": float(pct_change),
        "closes_5d": [float(c) for c in closes],
    }


def _realized_vol(closes: list[float]) -> float:
    if len(closes) < 2:
        return float("nan")
    arr = np.array(closes, dtype=float)
    log_returns = np.diff(np.log(arr))
    return float(np.std(log_returns, ddof=1) * np.sqrt(252) * 100.0)


def _yield_rate_of_change(closes: list[float]) -> str:
    if len(closes) < 4:
        return "unknown"
    bps_moves = [(closes[i] - closes[i - 1]) * 10.0 for i in range(-3, 0)]
    abs_moves = [abs(m) for m in bps_moves]
    return "accelerating" if abs_moves[-1] > abs_moves[0] else "flat"


def _term_structure(vix9d: float, vix: float, vix3m: float) -> str:
    if vix9d < vix < vix3m:
        return "contango"
    if vix9d > vix > vix3m:
        return "backwardation"
    return "mixed"


def _vix_spread_label(spread: float) -> str:
    """Near-term fear gauge from the VIX9D − VIX spread."""
    if spread > 1.0:
        return "Elevated Near-Term Fear"
    if spread < -1.0:
        return "Near-Term Calm"
    return "Neutral"


def _dxy_premarket() -> tuple[float | None, str]:
    """DXY (DX-Y.NYB) premarket move as a percent, plus a regime label.

    Returns (None, "Unavailable") if the fetch fails or the data is missing —
    yfinance occasionally returns an empty frame for the ICE dollar index.
    """
    try:
        snap = _ticker_snapshot("DX-Y.NYB")
        current = snap["current_price"]
        prior = snap["previous_close"]
        if not prior or np.isnan(prior) or np.isnan(current):
            return None, "Unavailable"
        change = (current - prior) / prior * 100.0
    except Exception as e:
        console.print(f"[red]DXY fetch failed:[/red] {e}")
        return None, "Unavailable"

    if change > 0.3:
        label = "Dollar Strength — headwind"
    elif change < -0.3:
        label = "Dollar Weakness — tailwind"
    else:
        label = "Dollar Neutral"
    return change, label


def fetch_market_snapshot() -> dict:
    snapshots = {t: _ticker_snapshot(t) for t in MACRO + SEMIS}

    tnx = snapshots["^TNX"]
    yield_bps_change = (tnx["current_price"] - tnx["previous_close"]) * 10.0
    yield_roc = _yield_rate_of_change(tnx["closes_5d"])

    smh_pct = snapshots["SMH"]["pct_change"]
    spy_pct = snapshots["SPY"]["pct_change"]
    qqq_pct = snapshots["QQQ"]["pct_change"]

    semi_vs_spy = {}
    underperformers = []
    for sym in ["SMH", "NVDA", "AMD", "TSM", "ASML", "INTC"]:
        diff = snapshots[sym]["pct_change"] - spy_pct
        semi_vs_spy[sym] = diff
        if diff < -UNDERPERFORM_THRESHOLD_PCT:
            underperformers.append(sym)

    term = _term_structure(
        snapshots["^VIX9D"]["current_price"],
        snapshots["^VIX"]["current_price"],
        snapshots["^VIX3M"]["current_price"],
    )

    vix9d_level = snapshots["^VIX9D"]["current_price"]
    vix_level_for_spread = snapshots["^VIX"]["current_price"]
    vix_spread = vix9d_level - vix_level_for_spread
    vix_spread_label = _vix_spread_label(vix_spread)

    dxy_change, dxy_label = _dxy_premarket()

    smh_rv = _realized_vol(snapshots["SMH"]["closes_5d"])
    vix_level = snapshots["^VIX"]["current_price"]
    rv_vix_ratio = smh_rv / vix_level if vix_level else float("nan")

    result = {
        "snapshots": snapshots,
        "yield_bps_change": yield_bps_change,
        "yield_roc": yield_roc,
        "smh_vs_spy": smh_pct - spy_pct,
        "smh_vs_qqq": smh_pct - qqq_pct,
        "semi_vs_spy": semi_vs_spy,
        "underperformers": underperformers,
        "vix_term_structure": term,
        "vix_spread": vix_spread,
        "vix_spread_label": vix_spread_label,
        "dxy_change": dxy_change,
        "dxy_label": dxy_label,
        "smh_realized_vol_5d": smh_rv,
        "realized_vol_vs_vix": rv_vix_ratio,
    }

    _print_confirmation(result)
    return result


def _print_confirmation(r: dict) -> None:
    console.print("[bold green]Market snapshot fetched[/bold green]")
    console.print(f"  TNX yield Δ: {r['yield_bps_change']:+.2f} bps ({r['yield_roc']})")
    console.print(f"  SMH vs SPY: {r['smh_vs_spy']:+.3f}%")
    console.print(f"  SMH vs QQQ: {r['smh_vs_qqq']:+.3f}%")
    for sym, diff in r["semi_vs_spy"].items():
        flag = " [red]UNDERPERFORM[/red]" if sym in r["underperformers"] else ""
        console.print(f"  {sym} vs SPY: {diff:+.3f}%{flag}")
    console.print(f"  VIX term structure: {r['vix_term_structure']}")
    console.print(
        f"  VIX9D/VIX spread: {r['vix_spread']:+.2f} ({r['vix_spread_label']})"
    )
    if r["dxy_change"] is None:
        console.print("  DXY premarket: Unavailable")
    else:
        console.print(
            f"  DXY premarket: {r['dxy_change']:+.2f}% ({r['dxy_label']})"
        )
    console.print(f"  SMH 5d realized vol: {r['smh_realized_vol_5d']:.2f}%")
    console.print(f"  RV / VIX ratio: {r['realized_vol_vs_vix']:.3f}")


if __name__ == "__main__":
    fetch_market_snapshot()
