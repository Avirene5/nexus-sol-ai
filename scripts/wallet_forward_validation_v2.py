"""Correctly flatten real Birdeye forward checkpoints and validate wallet persistence.

The upstream forward-performance dataset stores returns inside nested checkpoints.
This validator converts each observed checkpoint into a time-stamped observation.
No synthetic values, no random split, and no verified/trading claim.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any

DATA = Path("data")
INPUT = DATA / "wallet_forward_performance.json"
OUTPUT = DATA / "wallet_forward_validation_v2.json"


def num(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def flatten(payload: dict[str, Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for row in payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        wallet = row.get("wallet")
        token = row.get("token")
        entry_ts = num(row.get("entry_unix_time"))
        checkpoints = row.get("checkpoints") or {}
        if not wallet or entry_ts is None or not isinstance(checkpoints, dict):
            continue
        for horizon, checkpoint in checkpoints.items():
            if not isinstance(checkpoint, dict) or checkpoint.get("status") != "OBSERVED":
                continue
            checkpoint_ts = num(checkpoint.get("checkpoint_unix_time"))
            ret = num(checkpoint.get("return_pct"))
            if checkpoint_ts is None or ret is None:
                continue
            observations.append({
                "wallet": wallet,
                "token": token,
                "entry_ts": entry_ts,
                "ts": checkpoint_ts,
                "horizon": horizon,
                "forward_return_pct": ret,
            })
    return sorted(observations, key=lambda x: (x["ts"], x["wallet"], x.get("token") or "", x["horizon"]))


def window(rows: list[dict[str, Any]], train_frac: float, test_frac: float) -> dict[str, Any]:
    n = len(rows)
    train_end = int(n * train_frac)
    test_end = min(n, train_end + max(1, int(n * test_frac)))
    train, test = rows[:train_end], rows[train_end:test_end]
    history: dict[str, list[float]] = {}
    for row in train:
        history.setdefault(row["wallet"], []).append(row["forward_return_pct"])
    ranked = sorted(
        ((wallet, mean(values), len(values)) for wallet, values in history.items() if len(values) >= 3),
        key=lambda x: (-x[1], -x[2], x[0]),
    )
    selected_wallets = {wallet for wallet, _, _ in ranked[:20]}
    selected = [r["forward_return_pct"] for r in test if r["wallet"] in selected_wallets]
    control = [r["forward_return_pct"] for r in test if r["wallet"] not in selected_wallets]
    return {
        "train_rows": len(train),
        "test_rows": len(test),
        "selected_wallet_count": len(selected_wallets),
        "selected_test_rows": len(selected),
        "control_test_rows": len(control),
        "selected_mean_return_pct": mean(selected) if selected else None,
        "control_mean_return_pct": mean(control) if control else None,
        "delta_mean_return_pct": mean(selected) - mean(control) if selected and control else None,
        "selected_median_return_pct": median(selected) if selected else None,
        "control_median_return_pct": median(control) if control else None,
        "selected_wallets": [
            {"wallet": wallet, "train_mean_return_pct": avg, "train_observations": count}
            for wallet, avg, count in ranked
        ],
        "test_start_ts": test[0]["ts"] if test else None,
        "test_end_ts": test[-1]["ts"] if test else None,
    }


def main() -> None:
    if not INPUT.exists():
        raise RuntimeError("Missing real forward-performance dataset")
    payload = json.loads(INPUT.read_text(encoding="utf-8"))
    if payload.get("real_api_records") is not True:
        raise RuntimeError("Forward dataset is not marked as real API data")
    if int(payload.get("synthetic_values", 0) or 0) != 0:
        raise RuntimeError("Synthetic values detected; validation refused")
    rows = flatten(payload)
    specs = [(0.50, 0.10), (0.60, 0.10), (0.70, 0.10), (0.80, 0.10)]
    windows = [window(rows, a, b) for a, b in specs if len(rows) >= 20]
    deltas = [w["delta_mean_return_pct"] for w in windows if w["delta_mean_return_pct"] is not None]
    positive = sum(1 for d in deltas if d > 0)
    result = {
        "schema_version": "1.0",
        "method": "EXPANDING_MULTI_WINDOW_WALK_FORWARD_ON_FLATTENED_REAL_CHECKPOINTS",
        "source_file": str(INPUT),
        "source_provider": payload.get("source"),
        "source_endpoint": payload.get("source_endpoint"),
        "real_api_records": True,
        "synthetic_values": 0,
        "verification_status": "NOT_VERIFIED",
        "observed_checkpoint_rows": len(rows),
        "window_count": len(windows),
        "positive_delta_windows": positive,
        "positive_delta_fraction": positive / len(deltas) if deltas else None,
        "windows": windows,
        "cost_model": "NOT_APPLIED_NO_EXPLICIT_COST_FIELDS",
        "minimum_requirements_for_verified_claim": {
            "minimum_windows": 4,
            "minimum_positive_window_fraction": 0.75,
            "requires_real_api_records": True,
            "requires_zero_synthetic_values": True,
            "requires_sufficient_test_rows_each_window": True,
            "requires_independent_validation_before_live_signal": True,
            "requires_explicit_cost_or_slippage_data_for_net_edge": True,
        },
        "note": "Checkpoint returns are real observed Birdeye historical prices. This file makes no predictive or trading claim and does not infer missing costs.",
    }
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
