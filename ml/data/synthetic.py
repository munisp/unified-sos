"""Realistic Nigerian-context synthetic data generators.

Every generator is deterministic given ``seed``, size-configurable, and
writes Parquet via pandas+pyarrow, falling back to CSV when pyarrow is not
installed (so the training stack stays runnable in minimal environments).

Distributions are calibrated to plausible Nigerian public-finance ranges
(amounts in kobo; NGN 1 = 100 kobo). These are *training stand-ins*: the
honest path to production weights is the continuous trainer consuming
lakehouse extracts (see ml/README.md).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

NG_STATES = [
    "lagos", "kano", "rivers", "oyo", "kaduna", "enugu", "abuja_fct",
    "delta", "borno", "anambra",
]

# Median daily informal income proxies per state (NGN), rough ordering by
# observed IGR-per-capita rankings (Lagos top, Borno bottom).
STATE_INCOME_NGN = {
    "lagos": 9500, "abuja_fct": 9000, "rivers": 8200, "delta": 7600,
    "oyo": 6100, "anambra": 6400, "enugu": 5800, "kaduna": 5200,
    "kano": 4800, "borno": 3600,
}

LEVY_THRESHOLD_KOBO = 500_000  # NGN 5,000: split-payment evasion threshold


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def write_frame(df: pd.DataFrame, path: Path) -> Path:
    """Write Parquet, fall back to CSV when no parquet engine is available."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pyarrow  # noqa: F401

        df.to_parquet(path, index=False)
        return path
    except ImportError:
        fallback = path.with_suffix(".csv")
        df.to_csv(fallback, index=False)
        return fallback


def read_frame(path: Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix == ".parquet" and path.exists():
        try:
            return pd.read_parquet(path)
        except ImportError:
            csv = path.with_suffix(".csv")
            if csv.exists():
                return pd.read_csv(csv)
            raise
    csv = path.with_suffix(".csv")
    if csv.exists():
        return pd.read_csv(csv)
    raise FileNotFoundError(f"no parquet or csv found for {path}")


def dataset_hash(df: pd.DataFrame) -> str:
    """Stable hash of a frame for dataset lineage in model cards."""
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()[:16]


# ---------------------------------------------------------------- fraud ----
def generate_transactions(n: int = 4000, seed: int = 42, fraud_rate: float = 0.06) -> pd.DataFrame:
    """Payment/levy transactions with injected fraud patterns.

    Fraud archetypes (labels emitted by the generator rule, never learned):
      1. ghost-worker payroll rings: many 'payers' funnel salary-like
         round amounts to one beneficiary through one agent.
      2. split-payment evasion: bursts of payments just under the
         NGN 5,000 levy threshold from the same payer, same day.
      3. agent collusion clusters: an agent whose payers overwhelmingly
         settle to a tiny set of beneficiaries at odd hours.
      4. round-tripping: A pays B pays C pays A within a short window.

    Honest columns: payer_ref, agent_ref, beneficiary_ref, amount_kobo,
    ts (ISO UTC), state, fraud (0/1), pattern (str).
    """
    rng = _rng(seed)
    n_fraud = int(n * fraud_rate)
    n_legit = n - n_fraud
    base_ts = np.datetime64("2024-01-01T00:00:00")
    rows = []

    for i in range(n_legit):
        state = NG_STATES[rng.integers(len(NG_STATES))]
        income = STATE_INCOME_NGN[state]
        # Legit levies/fees: lognormal around ~1-3% of monthly informal income.
        amount = float(rng.lognormal(mean=np.log(income * 100 * 0.02), sigma=0.7))
        hour = int(rng.integers(7, 20))  # business hours
        ts = base_ts + np.timedelta64(int(rng.integers(0, 90)), "D") + np.timedelta64(hour, "h")
        rows.append(dict(
            payer_ref=f"P{rng.integers(0, 3000)}", agent_ref=f"A{rng.integers(0, 200)}",
            beneficiary_ref=f"B{rng.integers(0, 400)}",
            amount_kobo=max(5_000, amount), ts=str(ts), state=state,
            fraud=0, pattern="legit",
        ))

    quota = n_fraud // 4
    # 1. ghost-worker payroll rings
    for ring in range(max(1, quota // 12)):
        agent, ben = f"AG_F{ring}", f"BG_F{ring}"
        salary = float(rng.uniform(70_000, 180_000)) * 100  # round-ish payroll kobo
        for j in range(12):
            ts = base_ts + np.timedelta64(int(rng.integers(0, 90)), "D") + np.timedelta64(2, "h")
            rows.append(dict(payer_ref=f"PG{ring}_{j}", agent_ref=agent,
                             beneficiary_ref=ben, amount_kobo=round(salary, -3),
                             ts=str(ts), state="abuja_fct", fraud=1, pattern="ghost_payroll"))
    # 2. split-payment evasion: ~6 payments of ~NGN 4,000-4,900 same payer/day
    n_split = max(0, quota - len([r for r in rows if r["pattern"] == "ghost_payroll"]))
    for k in range(n_split // 6):
        payer = f"PS_{k}"
        day = int(rng.integers(0, 90))
        for j in range(6):
            ts = base_ts + np.timedelta64(day, "D") + np.timedelta64(int(rng.integers(8, 18)), "h")
            rows.append(dict(payer_ref=payer, agent_ref=f"A{rng.integers(0, 200)}",
                             beneficiary_ref=f"B{rng.integers(0, 400)}",
                             amount_kobo=float(rng.uniform(400_000, 490_000)),
                             ts=str(ts), state="lagos", fraud=1, pattern="split_payment"))
    # 3. agent collusion clusters
    n_coll = quota
    for k in range(n_coll // 10):
        agent = f"AC_{k}"
        bens = [f"BC_{k}_{i}" for i in range(2)]
        for j in range(10):
            ts = base_ts + np.timedelta64(int(rng.integers(0, 90)), "D") + np.timedelta64(int(rng.integers(0, 5)), "h")
            rows.append(dict(payer_ref=f"PC{rng.integers(0, 500)}", agent_ref=agent,
                             beneficiary_ref=bens[j % 2],
                             amount_kobo=float(rng.lognormal(np.log(300_000), 0.5)),
                             ts=str(ts), state="rivers", fraud=1, pattern="agent_collusion"))
    # 4. round-tripping A→B→C→A
    for k in range(quota // 3):
        a, b, c = f"PA_{k}", f"PB_{k}", f"PCx_{k}"
        amt = float(rng.uniform(1_000_000, 5_000_000))
        day = int(rng.integers(0, 88))
        for hop, (src, dst) in enumerate([(a, b), (b, c), (c, a)]):
            ts = base_ts + np.timedelta64(day + hop, "D") + np.timedelta64(12, "h")
            rows.append(dict(payer_ref=src, agent_ref=f"A{rng.integers(0, 200)}",
                             beneficiary_ref=dst, amount_kobo=amt,
                             ts=str(ts), state="kano", fraud=1, pattern="round_trip"))

    df = pd.DataFrame(rows).sample(frac=1.0, random_state=seed).reset_index(drop=True)
    return df


def build_fraud_graph(df: pd.DataFrame) -> dict:
    """Build payer/agent/beneficiary node graph tensors for the fraud GNN.

    Nodes: union of payer/agent/beneficiary refs. Node features:
      [log(total_amount), txn_count_log, unique_counterparties_log,
       mean_hour_norm, under_threshold_ratio, node_type_onehot(3)]
    Edges: (payer->agent, agent->beneficiary) per transaction.
    Labels: node is fraud=1 if it participates in any fraud transaction.
    """
    refs = pd.concat([df.payer_ref, df.agent_ref, df.beneficiary_ref]).unique()
    idx = {r: i for i, r in enumerate(refs)}
    n = len(refs)

    df = df.copy()
    df["hour"] = pd.to_datetime(df.ts).dt.hour
    feats = np.zeros((n, 8), dtype=np.float32)
    labels = np.zeros(n, dtype=np.int64)
    for r, i in idx.items():
        sub = df[(df.payer_ref == r) | (df.agent_ref == r) | (df.beneficiary_ref == r)]
        feats[i, 0] = np.log1p(sub.amount_kobo.sum()) / 20.0
        feats[i, 1] = np.log1p(len(sub)) / 5.0
        cps = pd.concat([sub.payer_ref, sub.agent_ref, sub.beneficiary_ref]).nunique()
        feats[i, 2] = np.log1p(cps) / 5.0
        feats[i, 3] = sub.hour.mean() / 24.0
        feats[i, 4] = float((sub.amount_kobo < LEVY_THRESHOLD_KOBO).mean())
        feats[i, 5] = float(r.startswith("P"))
        feats[i, 6] = float(r.startswith("A"))
        feats[i, 7] = float(r.startswith("B"))
        labels[i] = int(sub.fraud.max())

    src, dst = [], []
    for row in df.itertuples():
        src += [idx[row.payer_ref], idx[row.agent_ref]]
        dst += [idx[row.agent_ref], idx[row.beneficiary_ref]]
    return dict(
        x=feats, edge_index=np.array([src, dst], dtype=np.int64),
        y=labels, node_refs=np.array(refs),
    )


# ---------------------------------------------------------------- credit ---
def generate_credit(n: int = 3000, seed: int = 7) -> pd.DataFrame:
    """Taxpayer/credit features for informal-sector credit scoring.

    Features: state, occupation (market_trader / artisan / transport /
    agro_processor / civil_servant), monthly_income_proxy_ngn (lognormal
    around state median), seasonality_index (traders spike in festive Q4),
    igr_percentile (state IGR per-capita percentile), txns_last_90d,
    avg_levy_kobo. Label: creditworthy — logistic rule on repayment
    capacity proxies + noise; ~55-65% positive.
    """
    rng = _rng(seed)
    occupations = ["market_trader", "artisan", "transport", "agro_processor", "civil_servant"]
    occ_income_mult = {"market_trader": 1.0, "artisan": 0.85, "transport": 0.9,
                       "agro_processor": 0.95, "civil_servant": 1.4}
    rows = []
    for _ in range(n):
        state = NG_STATES[rng.integers(len(NG_STATES))]
        occ = occupations[rng.integers(len(occupations))]
        income = float(rng.lognormal(np.log(STATE_INCOME_NGN[state] * 30), 0.5)) * occ_income_mult[occ]
        season = float(np.clip(rng.normal(1.0, 0.2) + (0.35 if occ == "market_trader" else 0.0), 0.4, 1.8))
        igr_pct = float(rng.beta(2, 5))
        txns = int(max(1, rng.poisson(income / 25_000)))
        levy = float(income * 100 * rng.uniform(0.005, 0.03))
        z = 2.0 * (np.log(income) - 11.2) + 1.2 * (season - 1.0) + 2.0 * igr_pct \
            + 0.25 * np.log1p(txns) - 1.6 + rng.normal(0, 0.3)
        creditworthy = int(1.0 / (1.0 + np.exp(-z)) > rng.uniform())
        rows.append(dict(state=state, occupation=occ,
                         monthly_income_proxy_ngn=income, seasonality_index=season,
                         igr_percentile=igr_pct, txns_last_90d=txns,
                         avg_levy_kobo=levy, creditworthy=creditworthy))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ luc ----
def generate_properties(n: int = 2500, seed: int = 11) -> pd.DataFrame:
    """Property / Land Use Charge (LUC) valuation features.

    Features: footprint_m2, floors, h3_cell (synthetic hex cell id),
    land_use (residential/commercial/industrial/mixed), state.
    Target: value_ngn — hedonic price model with location premium,
    ~log-linear; documented target R^2 >= 0.5 on the synthetic test split.
    """
    rng = _rng(seed)
    land_uses = ["residential", "commercial", "industrial", "mixed"]
    lu_mult = {"residential": 1.0, "commercial": 2.2, "industrial": 1.5, "mixed": 1.6}
    rows = []
    for _ in range(n):
        state = NG_STATES[rng.integers(len(NG_STATES))]
        h3 = f"87{rng.integers(0, 9999):04d}"
        lu = land_uses[rng.integers(len(land_uses))]
        footprint = float(rng.lognormal(np.log(180), 0.6))
        floors = int(np.clip(rng.poisson(1.2) + 1, 1, 12))
        loc_premium = (STATE_INCOME_NGN[state] / 5000.0) * float(rng.uniform(0.6, 1.6))
        value = (footprint * 120_000 + floors * footprint * 60_000) * lu_mult[lu] * loc_premium
        value *= float(rng.lognormal(0, 0.15))  # noise
        rows.append(dict(state=state, h3_cell=h3, land_use=lu,
                         footprint_m2=footprint, floors=floors,
                         location_premium=loc_premium, value_ngn=value))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------- crowd ---
def generate_crowd(seq_len: int = 24, n_series: int = 200, seed: int = 13) -> pd.DataFrame:
    """Crowd/density sequences (hourly density at venue cells).

    Each series: daily sinusoid + weekly uplift (market days) + noise;
    label is next-hour density (regression target used by the LSTM).
    Columns: series_id, step, density (0-1), plus the full window is
    reconstructed downstream from long-format rows.
    """
    rng = _rng(seed)
    rows = []
    for s in range(n_series):
        phase = float(rng.uniform(0, 2 * np.pi))
        amp = float(rng.uniform(0.2, 0.5))
        base = float(rng.uniform(0.2, 0.4))
        for t in range(seq_len):
            daily = np.sin(2 * np.pi * (t % 24) / 24 + phase)
            market_day = 0.25 if (t // 24) % 4 == 0 else 0.0
            density = float(np.clip(base + amp * max(daily, 0) + market_day * (t % 24 in (10, 11, 12))
                                    + rng.normal(0, 0.03), 0, 1))
            rows.append(dict(series_id=s, step=t, density=density))
    return pd.DataFrame(rows)


GENERATORS = {
    "transactions": generate_transactions,
    "credit": generate_credit,
    "properties": generate_properties,
    "crowd": generate_crowd,
}


def generate_all(out_dir: Path, seed: int = 42) -> dict[str, Path]:
    out = {}
    for name, gen in GENERATORS.items():
        df = gen(seed=seed) if name != "crowd" else gen(seed=seed)
        out[name] = write_frame(df, Path(out_dir) / f"{name}.parquet")
    return out
