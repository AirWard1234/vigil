from __future__ import annotations

import os
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
console = Console()

FINNHUB_BASE = "https://finnhub.io/api/v1"
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

ET = ZoneInfo("America/New_York")

SEMI_TICKERS = ["NVDA", "AMD", "TSM", "ASML", "INTC", "SMH"]
MACRO_TERMS = [
    "Fed", "Federal Reserve", "treasury", "yield", "inflation", "interest rate",
    "rates", "CPI", "NFP", "jobs", "FOMC", "economy", "GDP", "payrolls",
    "tariff", "macro", "bond", "dollar", "DXY",
]
EVENT_TERMS = ["CPI", "NFP", "FOMC", "GDP"]
EARNINGS_LOOKBACK_DAYS = 5

OVERNIGHT_START = time(23, 0)   # 11 PM ET previous day
OVERNIGHT_END = time(8, 30)     # 8:30 AM ET today

# Last 2 hours before the 9:30 AM ET open — these headlines get 2x weight.
PRE_OPEN_RECENT_START = time(7, 30)
PRE_OPEN_RECENT_END = time(9, 30)
RECENT_WEIGHT = 2.0

GUIDANCE_PAT = re.compile(
    r"\b(guidance|outlook|forecast|FY\d{0,4}|Q[1-4]|projects?|expects?|"
    r"raises?|lowers?|cuts?|boosts?|warns?|misses?|beats?|"
    r"revenue\s+forecast|full[- ]year)\b",
    re.IGNORECASE,
)

# Macro phrases that are bearish for MNQ even when FinBERT reads them as
# financially "positive" (e.g. hot CPI is good news for bond sellers, bad for
# Nasdaq longs).
MNQ_BEARISH_KEYWORDS = [
    "yield high", "yields rise", "yields surge", "yields climb",
    "rate hike", "rates higher", "inflation hot", "cpi above",
    "nfp beats", "jobs strong", "hawkish", "tightening", "dollar surge",
    "dxy rise", "bond selloff", "treasury selloff",
]
MNQ_BULLISH_KEYWORDS = [
    "yield falls", "yields drop", "yields decline", "rate cut",
    "dovish", "easing", "inflation cools", "cpi below", "nfp miss",
    "jobs weak", "soft landing", "pivot", "dollar weak", "dxy falls",
    "treasury rally",
]
MNQ_ADJUSTMENT_MIN_MAGNITUDE = 0.6  # ensure overrides carry weight even when FinBERT was neutral

_MACRO_PATTERNS = [
    (term, re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE))
    for term in MACRO_TERMS
]


def _get(path: str, params: dict) -> list | dict:
    p = {**params, "token": FINNHUB_API_KEY}
    r = requests.get(f"{FINNHUB_BASE}{path}", params=p, timeout=10)
    r.raise_for_status()
    return r.json()


def _overnight_window() -> tuple[datetime, datetime]:
    """[11 PM ET prior day, 8:30 AM ET today] as ET-aware datetimes."""
    today_et = datetime.now(ET).date()
    start = datetime.combine(today_et - timedelta(days=1), OVERNIGHT_START, tzinfo=ET)
    end = datetime.combine(today_et, OVERNIGHT_END, tzinfo=ET)
    return start, end


def _in_window(unix_ts: int, start: datetime, end: datetime) -> bool:
    if not unix_ts:
        return False
    dt = datetime.fromtimestamp(unix_ts, tz=ET)
    return start <= dt <= end


def _matched_terms(text: str) -> list[str]:
    return [term for term, pat in _MACRO_PATTERNS if pat.search(text)]


def _company_news(ticker: str, start: datetime, end: datetime) -> list[dict]:
    # Finnhub company-news takes calendar-day from/to; widen to 2 days then filter.
    data = _get("/company-news", {
        "symbol": ticker,
        "from": start.date().strftime("%Y-%m-%d"),
        "to": end.date().strftime("%Y-%m-%d"),
    })
    if not isinstance(data, list):
        return []
    out = []
    for a in data:
        ts = a.get("datetime") or 0
        if not _in_window(ts, start, end):
            continue
        out.append({
            "ticker": ticker,
            "headline": a.get("headline", ""),
            "datetime": datetime.fromtimestamp(ts, tz=ET).isoformat(),
            "source": a.get("source", ""),
            "summary": a.get("summary", ""),
        })
    return out


def _general_news(start: datetime, end: datetime) -> list[dict]:
    data = _get("/news", {"category": "general", "minId": 0})
    if not isinstance(data, list):
        console.print("[yellow]General news endpoint returned no list[/yellow]")
        return []
    total = len(data)
    in_window = 0
    matched = 0
    out = []
    # Finnhub /news only returns the latest ~100 articles; restricting to the
    # 11pm–8:30am overnight window misses everything when running outside that
    # window, and even misses overnight content when articles are sparse. Use a
    # 24-hour rolling window ending now, with `start` as a softer floor.
    now_et = datetime.now(ET)
    effective_start = min(start, now_et - timedelta(hours=24))
    effective_end = max(end, now_et)
    for a in data:
        ts = a.get("datetime") or 0
        if not _in_window(ts, effective_start, effective_end):
            continue
        in_window += 1
        text = f"{a.get('headline', '')} {a.get('summary', '')}"
        terms = _matched_terms(text)
        if not terms:
            continue
        matched += 1
        out.append({
            "headline": a.get("headline", ""),
            "datetime": datetime.fromtimestamp(ts, tz=ET).isoformat(),
            "source": a.get("source", ""),
            "summary": a.get("summary", ""),
            "matched_terms": terms,
        })
    console.print(
        f"[dim]Macro news debug: {total} fetched, {in_window} in overnight window, "
        f"{matched} matched macro terms[/dim]"
    )
    return out


def _earnings(today_et: date) -> list[dict]:
    start = today_et - timedelta(days=EARNINGS_LOOKBACK_DAYS)
    data = _get("/calendar/earnings", {
        "from": start.strftime("%Y-%m-%d"),
        "to": today_et.strftime("%Y-%m-%d"),
    })
    rows = (data or {}).get("earningsCalendar") or [] if isinstance(data, dict) else []
    out = []
    for r in rows:
        actual = r.get("epsActual")
        estimate = r.get("epsEstimate")
        surprise_pct = None
        if actual is not None and estimate not in (None, 0):
            surprise_pct = (actual - estimate) / abs(estimate) * 100.0
        symbol = r.get("symbol", "")
        out.append({
            "symbol": symbol,
            "date": r.get("date"),
            "eps_actual": actual,
            "eps_estimate": estimate,
            "surprise_pct": surprise_pct,
            "is_semi": symbol in SEMI_TICKERS,
        })
    return out


def _economic_events(today_et: date) -> list[dict]:
    try:
        data = _get("/calendar/economic", {
            "from": today_et.strftime("%Y-%m-%d"),
            "to": today_et.strftime("%Y-%m-%d"),
        })
    except requests.HTTPError as e:
        # Economic calendar requires premium on some plans — degrade gracefully.
        console.print(f"[yellow]Economic calendar unavailable:[/yellow] {e}")
        return []

    rows = (data or {}).get("economicCalendar") or [] if isinstance(data, dict) else []
    out = []
    for r in rows:
        if (r.get("country") or "").upper() != "US":
            continue
        name = r.get("event", "") or ""
        matched = next(
            (t for t in EVENT_TERMS if re.search(rf"\b{t}\b", name, re.IGNORECASE)),
            None,
        )
        if not matched:
            continue
        out.append({
            "event": name,
            "release_time": r.get("time"),
            "country": "US",
            "matched_term": matched,
        })
    return out


def fetch_overnight_sentiment() -> dict:
    result: dict = {
        "semi_headlines": [],
        "macro_headlines": [],
        "earnings_data": [],
        "todays_events": [],
        "semi_sentiment_score": 0.0,
        "macro_sentiment_score": 0.0,
        "top_3_semi_headlines": [],
        "top_3_macro_headlines": [],
        "guidance_cut_flag": {},
        "earnings_surprise_pct": {},
        "source": "FINNHUB",
    }

    if not FINNHUB_API_KEY:
        console.print("[yellow]FINNHUB_API_KEY missing — sentiment unavailable[/yellow]")
        result["source"] = "ESTIMATED"
        _print_confirmation(result)
        return result

    start, end = _overnight_window()
    today_et = datetime.now(ET).date()

    try:
        for t in SEMI_TICKERS:
            try:
                result["semi_headlines"].extend(_company_news(t, start, end))
            except Exception as e:
                console.print(f"[yellow]Company news failed for {t}:[/yellow] {e}")
    except Exception as e:
        console.print(f"[red]Semi headlines fetch failed:[/red] {e}")

    try:
        result["macro_headlines"] = _general_news(start, end)
    except Exception as e:
        console.print(f"[red]Macro headlines fetch failed:[/red] {e}")

    try:
        result["earnings_data"] = _earnings(today_et)
    except Exception as e:
        console.print(f"[red]Earnings fetch failed:[/red] {e}")

    try:
        result["todays_events"] = _economic_events(today_et)
    except Exception as e:
        console.print(f"[red]Economic events fetch failed:[/red] {e}")

    try:
        _enrich_with_finbert(result["semi_headlines"])
        _enrich_with_finbert(result["macro_headlines"])
        adj, kept = _apply_mnq_inversion(result["macro_headlines"])
        console.print(
            f"[dim]MNQ inversion: {adj} adjusted, {kept} kept as FinBERT scored[/dim]"
        )
        result["semi_sentiment_score"] = round(_aggregate_score(result["semi_headlines"]), 4)
        result["macro_sentiment_score"] = round(_aggregate_score(result["macro_headlines"]), 4)
        result["top_3_semi_headlines"] = _top_n(result["semi_headlines"], 3)
        result["top_3_macro_headlines"] = _top_n(result["macro_headlines"], 3)
        guidance, surprise = _detect_guidance(result["semi_headlines"], result["earnings_data"])
        result["guidance_cut_flag"] = guidance
        result["earnings_surprise_pct"] = surprise
    except Exception as e:
        console.print(f"[red]FinBERT scoring step failed:[/red] {e}")

    _print_confirmation(result)
    return result


def _print_confirmation(r: dict) -> None:
    console.print(
        f"[bold green]Sentiment snapshot fetched[/bold green] [dim]({r['source']})[/dim]"
    )
    semi_n = len(r["semi_headlines"])
    macro_n = len(r["macro_headlines"])
    semi_earnings = sum(1 for e in r["earnings_data"] if e["is_semi"])
    console.print(f"  Semi headlines:    {semi_n}  [dim](score: {r['semi_sentiment_score']:+.3f})[/dim]")
    console.print(f"  Macro headlines:   {macro_n}  [dim](score: {r['macro_sentiment_score']:+.3f})[/dim]")
    console.print(f"  Earnings (5d):     {len(r['earnings_data'])}  [dim](semis: {semi_earnings})[/dim]")
    if r["todays_events"]:
        names = ", ".join(e["matched_term"] for e in r["todays_events"])
        console.print(f"  Today's events:    [red]{names}[/red]")
    else:
        console.print("  Today's events:    none")

    cuts = [t for t, flag in r["guidance_cut_flag"].items() if flag]
    if cuts:
        console.print(f"  Guidance cuts:     [red]{', '.join(cuts)}[/red]")

    color_map = {"positive": "green", "negative": "red", "neutral": "dim"}
    if r["top_3_semi_headlines"]:
        console.print("  [bold]Top semi headlines:[/bold]")
        for h in r["top_3_semi_headlines"]:
            label = h.get("sentiment", "?")
            color = color_map.get(label, "white")
            console.print(
                f"    [{color}]{label:>8}[/{color}] "
                f"({h.get('confidence', 0):.2f})  "
                f"{h.get('ticker', '')}: {h.get('headline', '')[:90]}"
            )
    if r["top_3_macro_headlines"]:
        console.print("  [bold]Top macro headlines:[/bold]")
        for h in r["top_3_macro_headlines"]:
            label = h.get("sentiment", "?")
            color = color_map.get(label, "white")
            console.print(
                f"    [{color}]{label:>8}[/{color}] "
                f"({h.get('confidence', 0):.2f})  "
                f"{h.get('headline', '')[:90]}"
            )


_finbert_pipe = None
_finbert_load_failed = False


def _get_finbert():
    """Lazy-load FinBERT once per process. Returns None if load fails.

    Honors FINBERT_CACHE_DIR (set to the Railway volume in railway.toml) so the
    ~440MB model downloads once and persists across redeploys.
    """
    global _finbert_pipe, _finbert_load_failed
    if _finbert_pipe is not None or _finbert_load_failed:
        return _finbert_pipe
    cache_dir = os.getenv("FINBERT_CACHE_DIR") or os.getenv("HF_HOME") or None
    try:
        from transformers import pipeline
        pipe_kwargs = dict(
            task="sentiment-analysis",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            truncation=True,
            top_k=None,
        )
        if cache_dir:
            pipe_kwargs["model_kwargs"] = {"cache_dir": cache_dir}
        _finbert_pipe = pipeline(**pipe_kwargs)
        suffix = f" [dim](cache: {cache_dir})[/dim]" if cache_dir else ""
        console.print(f"[dim]FinBERT loaded (ProsusAI/finbert){suffix}[/dim]")
    except Exception as e:
        console.print(f"[red]FinBERT load failed:[/red] {e}")
        _finbert_load_failed = True
        _finbert_pipe = None
    return _finbert_pipe


def _score_texts(texts: list[str]) -> list[tuple[str, float]]:
    """Return [(label, confidence), ...] aligned with `texts`. Falls back to neutral."""
    if not texts:
        return []
    pipe = _get_finbert()
    if pipe is None:
        return [("neutral", 0.0)] * len(texts)
    try:
        raw = pipe(texts, truncation=True, batch_size=8)
    except Exception as e:
        console.print(f"[yellow]FinBERT batch scoring failed:[/yellow] {e}")
        return [("neutral", 0.0)] * len(texts)
    out: list[tuple[str, float]] = []
    for r in raw:
        scores = r if isinstance(r, list) else [r]
        best = max(scores, key=lambda x: x.get("score", 0.0))
        out.append((str(best["label"]).lower(), float(best["score"])))
    return out


def _signed_score(label: str, confidence: float) -> float:
    if label == "positive":
        return confidence
    if label == "negative":
        return -confidence
    return 0.0


def _weight_for_iso(iso_dt: str) -> float:
    try:
        dt = datetime.fromisoformat(iso_dt)
    except Exception:
        return 1.0
    t = dt.astimezone(ET).time() if dt.tzinfo else dt.time()
    if PRE_OPEN_RECENT_START <= t <= PRE_OPEN_RECENT_END:
        return RECENT_WEIGHT
    return 1.0


def _enrich_with_finbert(headlines: list[dict]) -> None:
    """In-place enrichment: adds sentiment, confidence, sentiment_score, weight."""
    if not headlines:
        return
    texts = []
    for h in headlines:
        head = h.get("headline", "") or ""
        summ = h.get("summary", "") or ""
        texts.append(f"{head}. {summ}".strip()[:1024])
    scores = _score_texts(texts)
    for h, (label, conf) in zip(headlines, scores):
        h["sentiment"] = label
        h["confidence"] = round(conf, 4)
        h["sentiment_score"] = round(_signed_score(label, conf), 4)
        h["weight"] = _weight_for_iso(h.get("datetime", ""))


def _apply_mnq_inversion(headlines: list[dict]) -> tuple[int, int]:
    """Override FinBERT direction when macro phrases have known MNQ polarity.

    Returns (adjusted_count, kept_count). When a bearish phrase matches we
    force the signed score negative; bullish forces positive. Bearish wins on
    a tie (defensive bias). Magnitude uses FinBERT confidence with a floor so
    overrides on FinBERT-neutral headlines still contribute meaningfully.
    """
    adjusted = 0
    kept = 0
    for h in headlines:
        text = f"{h.get('headline', '')} {h.get('summary', '')}".lower()
        bearish = any(kw in text for kw in MNQ_BEARISH_KEYWORDS)
        bullish = any(kw in text for kw in MNQ_BULLISH_KEYWORDS)
        if not (bearish or bullish):
            h["mnq_adjusted"] = False
            kept += 1
            continue
        magnitude = max(
            abs(h.get("sentiment_score", 0.0)),
            h.get("confidence", 0.0),
            MNQ_ADJUSTMENT_MIN_MAGNITUDE,
        )
        h["sentiment_score"] = round(-magnitude if bearish else magnitude, 4)
        h["mnq_adjusted"] = True
        adjusted += 1
    return adjusted, kept


def _aggregate_score(headlines: list[dict]) -> float:
    total_w = sum(h.get("weight", 1.0) for h in headlines)
    if total_w <= 0:
        return 0.0
    weighted = sum(h.get("sentiment_score", 0.0) * h.get("weight", 1.0) for h in headlines)
    return max(-1.0, min(1.0, weighted / total_w))


def _top_n(headlines: list[dict], n: int = 3) -> list[dict]:
    ranked = sorted(
        headlines,
        key=lambda h: abs(h.get("sentiment_score", 0.0)) * h.get("weight", 1.0),
        reverse=True,
    )
    keep = ("headline", "datetime", "source", "ticker",
            "sentiment", "confidence", "sentiment_score", "weight",
            "matched_terms", "mnq_adjusted")
    return [{k: h[k] for k in keep if k in h} for h in ranked[:n]]


def _detect_guidance(
    semi_headlines: list[dict],
    earnings_rows: list[dict],
) -> tuple[dict[str, bool], dict[str, float | None]]:
    """Return (guidance_cut_flag_per_ticker, earnings_surprise_pct_per_ticker)."""
    surprise: dict[str, float | None] = {}
    for r in earnings_rows:
        sym = r.get("symbol")
        if sym:
            surprise[sym] = r.get("surprise_pct")

    if not surprise:
        return {}, surprise

    tally: dict[str, dict[str, int]] = {t: {"cut": 0, "raise": 0} for t in surprise}
    for h in semi_headlines:
        t = h.get("ticker")
        if t not in tally:
            continue
        text = f"{h.get('headline', '')} {h.get('summary', '')}"
        if not GUIDANCE_PAT.search(text):
            continue
        label = h.get("sentiment", "neutral")
        if label == "negative":
            tally[t]["cut"] += 1
        elif label == "positive":
            tally[t]["raise"] += 1

    guidance_cut = {t: v["cut"] > v["raise"] for t, v in tally.items()}
    return guidance_cut, surprise


def score_sentiment(text: str) -> float:
    """Signed FinBERT score in [-1, 1] (positive = bullish, negative = bearish)."""
    label, conf = (_score_texts([text]) or [("neutral", 0.0)])[0]
    return _signed_score(label, conf)


if __name__ == "__main__":
    fetch_overnight_sentiment()
