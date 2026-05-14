"""Hidden Markov Model regime classifier for MNQ pre-market bias.

Four regimes are detected from a 7-feature daily vector. The HMM is fit fresh
on 2 years of rolling yfinance history on each weekly retrain and held in
memory only — no trained model is ever persisted to disk, so it can never
overfit to a stale historical window. `retrain()` is the Monday-morning hook;
`get_regime_model()` self-heals if the cached model predates this week.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from hmmlearn import hmm
from rich.console import Console

console = Console()

N_REGIMES = 4
TRAIN_PERIOD = "2y"
LIVE_PERIOD = "6mo"          # enough history for the semi-health rolling windows
REALIZED_VOL_WINDOW = 5      # closes — matches data/fetcher._realized_vol
HMM_ITERATIONS = 200
RANDOM_SEED = 42
MIN_TRAIN_ROWS = 100

REGIME_TRENDING_LOW_VOL = "Trending Low Vol"
REGIME_TRENDING_HIGH_VOL = "Trending High Vol"
REGIME_MEAN_REVERTING = "Mean Reverting"
REGIME_CHAOTIC = "Chaotic"

FEATURE_NAMES = [
    "vix_level",
    "vix_term_slope",
    "rv_iv_ratio",
    "yield_bps_change_abs",
    "semi_health",
    "smh_vs_qqq",
    "gex_encoded",
]

# Negative dealer gamma amplifies moves (-1); positive gamma suppresses them (+1).
GEX_ENCODING = {
    "amplifying": -1, "negative": -1,
    "neutral": 0, "unavailable": 0, "mixed": 0,
    "suppressing": 1, "positive": 1,
}

_TICKERS = ["^VIX", "^VIX9D", "^VIX3M", "^TNX", "SMH", "QQQ", "SPY"]


# --------------------------------------------------------------------------
# data fetch + feature construction
# --------------------------------------------------------------------------

def _download_closes(period: str) -> dict[str, pd.Series]:
    """Fresh daily closes for every ticker the feature vector needs."""
    raw = yf.download(
        _TICKERS, period=period, interval="1d",
        auto_adjust=True, progress=False, group_by="column",
    )
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    return {
        t: (closes[t].dropna() if t in closes else pd.Series(dtype=float))
        for t in _TICKERS
    }


def _realized_vol_series(closes: pd.Series, window: int) -> pd.Series:
    """Annualized realized vol in vol points, mirroring data/fetcher._realized_vol."""
    log_ret = np.log(closes).diff()
    return log_ret.rolling(window - 1).std(ddof=1) * np.sqrt(252) * 100.0


def _semi_health(smh: pd.Series, spy: pd.Series) -> pd.Series:
    """0-100 semiconductor health proxy from SMH momentum and relative strength.

    Built purely from price history so it is identical at train and inference
    time. Blends 20d relative strength vs SPY, 10d momentum, and a 50d trend
    filter.
    """
    rs = smh.pct_change(20, fill_method=None) - spy.pct_change(20, fill_method=None)
    mom = smh.pct_change(10, fill_method=None)
    above_ma = (smh > smh.rolling(50).mean()).astype(float)

    rs_c = (rs / 0.05).clip(-1, 1)
    mom_c = (mom / 0.05).clip(-1, 1)
    ma_c = above_ma * 2 - 1
    raw = 0.4 * rs_c + 0.4 * mom_c + 0.2 * ma_c
    return ((raw + 1) * 50).clip(0, 100)


def _gex_proxy(vix: pd.Series) -> pd.Series:
    """Historical GEX sign proxy — true dealer gamma is not available historically.

    Elevated and rising VIX coincides with dealers short gamma (amplifying, -1);
    low and easing VIX coincides with long gamma (suppressing, +1). The live
    classifier always overrides this with the real scraped GEX label.
    """
    ma20 = vix.rolling(20).mean()
    chg = vix.diff()
    proxy = pd.Series(0.0, index=vix.index)
    proxy[(vix > ma20) & (chg > 0)] = -1.0
    proxy[(vix < ma20) & (chg <= 0)] = 1.0
    return proxy


def _build_feature_frame(closes: dict[str, pd.Series]) -> pd.DataFrame:
    """Assemble the 7-feature daily matrix from raw close-price series."""
    vix = closes["^VIX"]
    df = pd.DataFrame(index=vix.index)
    df["vix_level"] = vix
    df["vix_term_slope"] = closes["^VIX3M"] - closes["^VIX9D"]
    df["rv_iv_ratio"] = _realized_vol_series(closes["SMH"], REALIZED_VOL_WINDOW) / vix
    df["yield_bps_change_abs"] = (closes["^TNX"].diff() * 10.0).abs()
    df["semi_health"] = _semi_health(closes["SMH"], closes["SPY"])
    df["smh_vs_qqq"] = (
        closes["SMH"].pct_change(fill_method=None)
        - closes["QQQ"].pct_change(fill_method=None)
    ) * 100.0
    df["gex_encoded"] = _gex_proxy(vix)
    return df[FEATURE_NAMES].replace([np.inf, -np.inf], np.nan).dropna()


# --------------------------------------------------------------------------
# model bundle
# --------------------------------------------------------------------------

@dataclass
class RegimeModel:
    """A fitted HMM plus everything needed to score a fresh feature row."""

    hmm: hmm.GaussianHMM
    state_to_label: dict[int, str]
    feat_mean: np.ndarray
    feat_std: np.ndarray
    trained_on: date
    n_samples: int

    def _standardize(self, X: np.ndarray) -> np.ndarray:
        return (X - self.feat_mean) / self.feat_std

    def predict(self, feature_row: np.ndarray) -> tuple[str, float]:
        """Return (regime_label, regime_confidence_pct) for one feature vector."""
        X = self._standardize(feature_row.reshape(1, -1))
        proba = self.hmm.predict_proba(X)[0]
        state = int(np.argmax(proba))
        return self.state_to_label[state], float(proba[state] * 100.0)


def _label_states(model: hmm.GaussianHMM) -> dict[int, str]:
    """Map the HMM's anonymous hidden states onto the four named regimes.

    States are placed on two axes from their (standardized) feature means:
    a volatility/stress axis and a follow-through axis. The two calmest states
    split into Trending Low Vol / Mean Reverting; the two most volatile split
    into Trending High Vol / Chaotic — higher follow-through wins the trending
    label in each pair. Deterministic and bijective.
    """
    means = model.means_
    idx = {name: i for i, name in enumerate(FEATURE_NAMES)}

    stress = (
        means[:, idx["vix_level"]]
        + means[:, idx["yield_bps_change_abs"]]
        - means[:, idx["vix_term_slope"]]      # backwardation (low slope) = stress
    )
    follow_through = (
        means[:, idx["rv_iv_ratio"]]           # realized outrunning implied
        - means[:, idx["gex_encoded"]]         # amplifying gamma (-1) trends
        + 0.5 * means[:, idx["semi_health"]]   # healthy semis sustain direction
    )

    order = np.argsort(stress)                 # calmest -> most stressed
    calm_pair = sorted(order[:2], key=lambda s: follow_through[s])
    stressed_pair = sorted(order[2:], key=lambda s: follow_through[s])

    return {
        int(calm_pair[0]): REGIME_MEAN_REVERTING,
        int(calm_pair[1]): REGIME_TRENDING_LOW_VOL,
        int(stressed_pair[0]): REGIME_CHAOTIC,
        int(stressed_pair[1]): REGIME_TRENDING_HIGH_VOL,
    }


# --------------------------------------------------------------------------
# training + weekly retrain
# --------------------------------------------------------------------------

def train_regime_model() -> RegimeModel:
    """Fetch 2y of rolling history fresh and fit a new HMM. Never persisted."""
    closes = _download_closes(TRAIN_PERIOD)
    frame = _build_feature_frame(closes)
    if len(frame) < MIN_TRAIN_ROWS:
        raise RuntimeError(f"insufficient training data: {len(frame)} rows")

    X = frame.to_numpy(dtype=float)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    X_std = (X - mean) / std

    model = hmm.GaussianHMM(
        n_components=N_REGIMES,
        covariance_type="diag",
        n_iter=HMM_ITERATIONS,
        random_state=RANDOM_SEED,
    )
    model.fit(X_std)

    bundle = RegimeModel(
        hmm=model,
        state_to_label=_label_states(model),
        feat_mean=mean,
        feat_std=std,
        trained_on=date.today(),
        n_samples=len(frame),
    )
    _print_training_confirmation(bundle, model.predict(X_std))
    return bundle


_MODEL_CACHE: RegimeModel | None = None


def _most_recent_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def get_regime_model(force_retrain: bool = False) -> RegimeModel:
    """Return the in-memory model, retraining if missing or older than this week.

    Self-heals: even if the Monday scheduler misses, the first call of the week
    triggers a fresh retrain so the model never serves a stale historical fit.
    """
    global _MODEL_CACHE
    stale = (
        _MODEL_CACHE is None
        or _MODEL_CACHE.trained_on < _most_recent_monday(date.today())
    )
    if force_retrain or stale:
        _MODEL_CACHE = train_regime_model()
    return _MODEL_CACHE


def retrain() -> RegimeModel:
    """Monday 8:00 AM ET scheduler hook — force a fresh fit before the 8:45 run."""
    return get_regime_model(force_retrain=True)


# --------------------------------------------------------------------------
# live classification
# --------------------------------------------------------------------------

def _live_feature_row(market: dict, gex: dict) -> np.ndarray:
    """Build today's 7-feature vector from the freshly fetched pipeline snapshots."""
    snaps = market.get("snapshots", {})
    vix = snaps.get("^VIX", {}).get("current_price", float("nan"))
    vix9d = snaps.get("^VIX9D", {}).get("current_price", float("nan"))
    vix3m = snaps.get("^VIX3M", {}).get("current_price", float("nan"))

    # semi_health needs price history — fetch a short window fresh.
    closes = _download_closes(LIVE_PERIOD)
    semi_health = float(_semi_health(closes["SMH"], closes["SPY"]).iloc[-1])

    gex_label = (gex or {}).get("gex_label", "neutral")

    return np.array([
        vix,
        vix3m - vix9d,
        market.get("realized_vol_vs_vix", float("nan")),
        abs(market.get("yield_bps_change", float("nan"))),
        semi_health,
        market.get("smh_vs_qqq", float("nan")),
        GEX_ENCODING.get(gex_label, 0),
    ], dtype=float)


def classify(market: dict, gex: dict | None = None) -> dict:
    """Classify today into a regime. Falls back to Chaotic (stay out) on failure.

    `market` is data.fetcher.fetch_market_snapshot(); `gex` is
    data.options.fetch_gex_snapshot().
    """
    try:
        model = get_regime_model()
        row = _live_feature_row(market, gex or {})
        if np.isnan(row).any():
            raise ValueError(f"incomplete live feature vector: {row.tolist()}")
        label, confidence = model.predict(row)
        result = {
            "regime_label": label,
            "regime_confidence": round(confidence, 1),
            "source": "HMM",
        }
    except Exception as e:
        console.print(f"[red]Regime classification failed:[/red] {e}")
        result = {
            "regime_label": REGIME_CHAOTIC,
            "regime_confidence": 0.0,
            "source": "ESTIMATED",
        }
    _print_classification(result)
    return result


# --------------------------------------------------------------------------
# console output
# --------------------------------------------------------------------------

def _print_training_confirmation(bundle: RegimeModel, states: np.ndarray) -> None:
    console.print(
        f"[bold green]HMM regime model trained[/bold green] "
        f"[dim]({bundle.n_samples} days, {TRAIN_PERIOD} rolling)[/dim]"
    )
    counts = pd.Series(states).map(bundle.state_to_label).value_counts()
    for regime in (
        REGIME_TRENDING_LOW_VOL, REGIME_TRENDING_HIGH_VOL,
        REGIME_MEAN_REVERTING, REGIME_CHAOTIC,
    ):
        n = int(counts.get(regime, 0))
        pct = n / bundle.n_samples * 100.0 if bundle.n_samples else 0.0
        console.print(f"  {regime:<20} {n:>4} days ({pct:4.1f}%)")


def _print_classification(r: dict) -> None:
    color = {
        REGIME_TRENDING_LOW_VOL: "green",
        REGIME_TRENDING_HIGH_VOL: "yellow",
        REGIME_MEAN_REVERTING: "yellow",
        REGIME_CHAOTIC: "red",
    }.get(r["regime_label"], "white")
    console.print(
        f"[bold {color}]Regime: {r['regime_label']}[/bold {color}] "
        f"[dim]({r['regime_confidence']:.1f}% confidence — {r['source']})[/dim]"
    )


if __name__ == "__main__":
    from data.fetcher import fetch_market_snapshot
    from data.options import fetch_gex_snapshot

    retrain()
    classify(fetch_market_snapshot(), fetch_gex_snapshot())
