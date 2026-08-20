"""Event-level walk-forward validation for real wallet forward observations.

Prevents leakage between multiple checkpoints belonging to the same wallet/token entry.
Uses only observed real API checkpoints and refuses synthetic data.
"""
from __future__ import annotations
import json, math
from pathlib import Path
from statistics import mean, median
from typing import Any

DATA = Path("data")
INPUT = DATA / "wallet_forward_performance.json"
OUTPUT = DATA / "wallet_forward_validation_v3.json"


def num(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def flatten(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for row in payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        wallet, token = row.get("wallet"), row.get("token")
        entry_ts = num(row.get("entry_unix_time"))
        checkpoints = row.get("checkpoints") or {}
        if not wallet or entry_ts is None or not isinstance(checkpoints, dict):
            continue
        for horizon, cp in checkpoints.items():
            if not isinstance(cp, dict) or cp.get("status") != "OBSERVED":
                continue
            ts, ret = num(cp.get("checkpoint_unix_time")), num(cp.get("return_pct"))
            if ts is None or ret is None:
                continue
            out.append({"wallet": wallet, "token": token, "entry_ts": entry_ts,
                        "ts": ts, "horizon": horizon, "forward_return_pct": ret})
    return sorted(out, key=lambda r: (r["entry_ts"], r["wallet"], r.get("token") or "", r["horizon"]))


def event_windows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Split on unique entry events, never in the middle of one entry's checkpoints.
    events = sorted({(r["entry_ts"], r["wallet"], r.get("token")) for r in rows})
    specs = [(0.50, 0.10), (0.60, 0.10), (0.70, 0.10), (0.80, 0.10)]
    results = []
    for train_frac, test_frac in specs:
        n = len(events)
        train_end = int(n * train_frac)
        test_end = min(n, train_end + max(1, int(n * test_frac)))
        train_keys, test_keys = set(events[:train_end]), set(events[train_end:test_end])
        train = [r for r in rows if (r["entry_ts"], r["wallet"], r.get("token")) in train_keys]
        test = [r for r in rows if (r["entry_ts"], r["wallet"], r.get("token")) in test_keys]
        history: dict[str, list[float]] = {}
        for r in train:
            history.setdefault(r["wallet"], []).append(r["forward_return_pct"])
        ranked = sorted(((w, mean(v), len(v)) for w, v in history.items() if len(v) >= 3),
                        key=lambda x: (-x[1], -x[2], x[0]))
        selected_wallets = {w for w, _, _ in ranked[:20]}
        selected = [r["forward_return_pct"] for r in test if r["wallet"] in selected_wallets]
        control = [r["forward_return_pct"] for r in test if r["wallet"] not in selected_wallets]
        delta = mean(selected) - mean(control) if selected and control else None
        results.append({
            "train_events": len(train_keys), "test_events": len(test_keys),
            "train_rows": len(train), "test_rows": len(test),
            "selected_wallet_count": len(selected_wallets),
            "selected_test_rows": len(selected), "control_test_rows": len(control),
            "selected_mean_return_pct": mean(selected) if selected else None,
            "control_mean_return_pct": mean(control) if control else None,
            "delta_mean_return_pct": delta,
            "selected_median_return_pct": median(selected) if selected else None,
            "control_median_return_pct": median(control) if control else None,
            "test_start_entry_ts": min((k[0] for k in test_keys), default=None),
            "test_end_entry_ts": max((k[0] for k in test_keys), default=None),
        })
    return results


def main() -> None:
    if not INPUT.exists():
        raise RuntimeError("Missing real forward-performance dataset")
    payload = json.loads(INPUT.read_text(encoding="utf-8"))
    if payload.get("real_api_records") is not True:
        raise RuntimeError("Forward dataset is not marked as real API data")
    if int(payload.get("synthetic_values", 0) or 0) != 0:
        raise RuntimeError("Synthetic values detected; validation refused")
    rows = flatten(payload)
    events = {(r["entry_ts"], r["wallet"], r.get("token")) for r in rows}
    windows = event_windows(rows) if len(events) >= 20 else []
    deltas = [w["delta_mean_return_pct"] for w in windows if w["delta_mean_return_pct"] is not None]
    result = {
        "schema_version": "1.0",
        "method": "EVENT_LEVEL_EXPANDING_MULTI_WINDOW_WALK_FORWARD",
        "source_file": str(INPUT), "source_provider": payload.get("source"),
        "source_endpoint": payload.get("source_endpoint"),
        "real_api_records": True, "synthetic_values": 0,
        "verification_status": "NOT_VERIFIED",
        "observed_checkpoint_rows": len(rows), "unique_entry_events": len(events),
        "window_count": len(windows),
        "positive_delta_windows": sum(1 for d in deltas if d > 0),
        "positive_delta_fraction": (sum(1 for d in deltas if d > 0) / len(deltas)) if deltas else None,
        "windows": windows,
        "cost_model": "NOT_APPLIED_NO_EXPLICIT_COST_FIELDS",
        "leakage_protection": "TRAIN_TEST_SPLIT_IS_AT_UNIQUE_ENTRY_EVENT_LEVEL",
        "note": "Observed checkpoint returns only. No synthetic values, no predictive/trading claim, and no inferred transaction costs.",
    }
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
