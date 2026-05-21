"""One-off diagnostic for the 'always Mean Reverting @ 100%' HMM bug.

Replicates classifier/regime.py's exact training + inference path and prints
every internal the model produces so we can see whether the states have
collapsed or the posterior is degenerate.
"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from hmmlearn import hmm

from classifier import regime as R

np.set_printoptions(precision=3, suppress=True, linewidth=140)
pd.set_option("display.width", 140)
pd.set_option("display.max_columns", 20)

LINE = "=" * 78


def fit_model(X_std: np.ndarray, n_components: int, cov: str, n_iter: int):
    model = hmm.GaussianHMM(
        n_components=n_components,
        covariance_type=cov,
        n_iter=n_iter,
        random_state=R.RANDOM_SEED,
    )
    model.fit(X_std)
    return model


def report(model, X, X_std, mean, std, n_components: int) -> None:
    states = model.predict(X_std)

    # --- (1) per-state mean vectors -------------------------------------
    print(LINE)
    print(f"(1) STATE MEAN VECTORS  ({n_components} states)")
    print(LINE)
    print("Standardized space (what the HMM actually fits on):")
    means_df = pd.DataFrame(model.means_, columns=R.FEATURE_NAMES)
    means_df.index.name = "state"
    print(means_df)
    print("\nDe-standardized to real feature units:")
    real_means = model.means_ * std + mean
    real_df = pd.DataFrame(real_means, columns=R.FEATURE_NAMES)
    real_df.index.name = "state"
    print(real_df)

    # pairwise distance between state means tells us if states are distinct
    print("\nPairwise L2 distance between standardized state means:")
    for i in range(n_components):
        for j in range(i + 1, n_components):
            d = np.linalg.norm(model.means_[i] - model.means_[j])
            print(f"  state {i} <-> state {j}:  {d:.3f}")

    # --- (2) state distribution ----------------------------------------
    print()
    print(LINE)
    print(f"(2) STATE DISTRIBUTION  ({len(X)} training days)")
    print(LINE)
    counts = pd.Series(states).value_counts().sort_index()
    collapsed = False
    for s in range(n_components):
        n = int(counts.get(s, 0))
        pct = n / len(X) * 100.0
        bar = "#" * int(pct / 2)
        flag = "  <-- DOMINATES" if pct > 80 else ""
        if pct > 80:
            collapsed = True
        print(f"  state {s}: {n:>4} days ({pct:5.1f}%) {bar}{flag}")

    # --- (5) state -> regime label mapping -----------------------------
    print()
    print(LINE)
    print("(5) STATE INDEX -> REGIME LABEL  (_label_states)")
    print(LINE)
    mapping = R._label_states(model) if n_components == 4 else None
    if mapping is None:
        print("  _label_states only supports 4 states - skipped for this run")
    else:
        for s in range(n_components):
            n = int(counts.get(s, 0))
            print(f"  state {s} -> {mapping[s]:<20} ({n} days)")

    # --- transition matrix + start probabilities -----------------------
    print()
    print(LINE)
    print("(*) startprob_ and transmat_  (the single-observation smoking gun)")
    print(LINE)
    print("startprob_ :", model.startprob_)
    print("transmat_  :")
    print(model.transmat_)

    # stationary distribution of the transition matrix
    vals, vecs = np.linalg.eig(model.transmat_.T)
    stat = np.real(vecs[:, np.argmin(np.abs(vals - 1.0))])
    stat = stat / stat.sum()
    print("stationary distribution of transmat_ :", stat)

    return states, mapping, collapsed


def inference_check(model, X, mean, std, mapping, n_components: int) -> None:
    # --- (3) today's feature vector ------------------------------------
    print()
    print(LINE)
    print("(3) TODAY'S FEATURE VECTOR  (last row of the rolling window)")
    print(LINE)
    today = X[-1]
    for name, val in zip(R.FEATURE_NAMES, today):
        print(f"  {name:<24} {val:>12.4f}")
    today_std = (today - mean) / std
    print("  standardized           ", np.array2string(today_std))

    # --- (4) raw posterior over ALL states -----------------------------
    print()
    print(LINE)
    print("(4) RAW POSTERIOR PROBABILITIES  (before argmax)")
    print(LINE)
    proba = model.predict_proba(today_std.reshape(1, -1))[0]
    for s in range(n_components):
        label = mapping[s] if mapping else f"state {s}"
        print(f"  state {s} ({label:<20}) : {proba[s]:.6f}")
    winner = int(np.argmax(proba))
    print(f"\n  argmax -> state {winner} = "
          f"{mapping[winner] if mapping else winner}  @ {proba[winner]*100:.1f}%")

    # also show what predict_proba does if startprob were uniform/stationary
    print("\n  --- emission-only posterior (ignoring degenerate startprob) ---")
    log_em = model._compute_log_likelihood(today_std.reshape(1, -1))[0]
    em = np.exp(log_em - log_em.max())
    em = em / em.sum()
    for s in range(n_components):
        label = mapping[s] if mapping else f"state {s}"
        print(f"  state {s} ({label:<20}) : {em[s]:.6f}")


def main() -> None:
    print("Downloading 2y rolling history (same as train_regime_model)...")
    closes = R._download_closes(R.TRAIN_PERIOD)
    frame = R._build_feature_frame(closes)
    X = frame.to_numpy(dtype=float)
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std[std == 0] = 1.0
    X_std = (X - mean) / std
    print(f"Feature frame: {len(X)} rows x {X.shape[1]} features\n")

    # ---- primary run: exactly what regime.py does today -----------------
    print("\n" + "#" * 78)
    print(f"# PRIMARY RUN  n_components={R.N_REGIMES}  "
          f"cov=diag  n_iter={R.HMM_ITERATIONS}  (current production config)")
    print("#" * 78 + "\n")
    model = fit_model(X_std, R.N_REGIMES, "diag", R.HMM_ITERATIONS)
    _, mapping, collapsed = report(model, X, X_std, mean, std, R.N_REGIMES)
    inference_check(model, X, mean, std, mapping, R.N_REGIMES)

    # ---- post-fix verification ----------------------------------------
    print("\n\n" + "#" * 78)
    print("# POST-FIX  startprob_ <- stationary distribution of transmat_")
    print("#" * 78 + "\n")
    model.startprob_ = R._stationary_distribution(model.transmat_)
    print("startprob_ is now:", model.startprob_, "\n")
    inference_check(model, X, mean, std, mapping, R.N_REGIMES)

    # ---- experiment: n_components=3 ------------------------------------
    print("\n\n" + "#" * 78)
    print("# EXPERIMENT  n_components=3  cov=diag  n_iter=200")
    print("#" * 78 + "\n")
    model3 = fit_model(X_std, 3, "diag", 200)
    states3, _, _ = report(model3, X, X_std, mean, std, 3)
    inference_check(model3, X, mean, std, None, 3)


if __name__ == "__main__":
    main()
