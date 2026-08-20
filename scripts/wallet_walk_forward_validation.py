"""Multi-window walk-forward validation using real historical wallet outcomes only."""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any

DATA = Path("data")
OUT = DATA / "wallet_walk_forward_validation.json"


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"real_api_records": False, "synthetic_values": 0, "rows": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"real_api_records": False, "synthetic_values": 0, "rows": []}


def num(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def first_num(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        x = num(row.get(k))
        if x is not None:
            return x
    return None


def wallet(row: dict[str, Any]) -> str | None:
    return row.get("wallet") or row.get("wallet_address") or row.get("trader")


def ts(row: dict[str, Any]) -> float | None:
    return first_num(row, ("signal_ts", "signal_time", "first_trade_ts", "timestamp", "ts"))


def raw_return(row: dict[str, Any]) -> float | None:
    return first_num(row, ("forward_return", "forward_return_pct", "return_pct", "outcome_return"))


def adjusted_return(row: dict[str, Any]) -> tuple[float | None, str]:
    """Use only explicit real cost/slippage fields; never invent a liquidity cost."""
    r = raw_return(row)
    if r is None:
        return None, "missing_return"
    explicit = first_num(row, ("total_cost_return", "cost_return", "round_trip_cost_return"))
    if explicit is not None:
        return r - explicit, "explicit_cost_return"
    slip_bps = first_num(row, ("slippage_bps", "round_trip_slippage_bps"))
    fee_bps = first_num(row, ("fee_bps", "round_trip_fee_bps"))
    if slip_bps is not None or fee_bps is not None:
        return r - ((slip_bps or 0.0) + (fee_bps or 0.0)) / 10000.0, "explicit_slippage_fee_bps"
    provided = first_num(row, ("liquidity_adjusted_return", "cost_adjusted_return"))
    if provided is not None:
        return provided, "upstream_adjusted_return"
    return r, "unadjusted_missing_cost_fields"


def clean_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows", []) or payload.get("candidates", [])
    clean: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        w, t, r = wallet(row), ts(row), raw_return(row)
        adj, method = adjusted_return(row)
        if w and t is not None and r is not None and adj is not None:
            clean.append({"wallet": w, "ts": t, "raw_return": r, "eval_return": adj, "adjustment": method})
    return sorted(clean, key=lambda x: (x["ts"], x["wallet"]))


def window(clean: list[dict[str, Any]], train_frac: float, test_frac: float) -> dict[str, Any]:
    n = len(clean)
    train_end = int(n * train_frac)
    test_end = min(n, train_end + max(1, int(n * test_frac)))
    train, test = clean[:train_end], clean[train_end:test_end]
    by_wallet: dict[str, list[float]] = {}
    for row in train:
        by_wallet.setdefault(row["wallet"], []).append(row["eval_return"])
    ranked = sorted(((w, mean(v), len(v)) for w, v in by_wallet.items() if len(v) >= 3), key=lambda x: (-x[1], -x[2], x[0]))
    selected_wallets = {w for w, _, _ in ranked[: min(20, len(ranked))]}
    selected = [r["eval_return"] for r in test if r["wallet"] in selected_wallets]
    control = [r["eval_return"] for r in test if r["wallet"] not in selected_wallets]
    raw_selected = [r["raw_return"] for r in test if r["wallet"] in selected_wallets]
    raw_control = [r["raw_return"] for r in test if r["wallet"] not in selected_wallets]
    return {
        "train_fraction": train_frac, "test_fraction": test_frac,
        "train_rows": len(train), "test_rows": len(test),
        "selected_wallet_count": len(selected_wallets),
        "selected_test_rows": len(selected), "control_test_rows": len(control),
        "selected_mean": mean(selected) if selected else None,
        "control_mean": mean(control) if control else None,
        "delta_mean": (mean(selected) - mean(control)) if selected and control else None,
        "selected_median": median(selected) if selected else None,
        "control_median": median(control) if control else None,
        "raw_selected_mean": mean(raw_selected) if raw_selected else None,
        "raw_control_mean": mean(raw_control) if raw_control else None,
        "selected_wallets": [{"wallet": w, "train_mean": m, "train_observations": c} for w, m, c in ranked[:20]],
        "test_start_ts": test[0]["ts"] if test else None,
        "test_end_ts": test[-1]["ts"] if test else None,
    }


def main() -> None:
    candidates = [DATA / "wallet_forward_performance.json", DATA / "forward_performance.json"]
    source = next((p for p in candidates if p.exists()), None)
    payload = load(source) if source else {"real_api_records": False, "synthetic_values": 0, "rows": []}
    clean = clean_rows(payload)
    specs = [(0.50, 0.10), (0.60, 0.10), (0.70, 0.10), (0.80, 0.10)]
    windows = [window(clean, a, b) for a, b in specs if len(clean) >= 20]
    deltas = [w["delta_mean"] for w in windows if w["delta_mean"] is not None]
    positive = sum(1 for d in deltas if d > 0)
    methods: dict[str, int] = {}
    for row in clean:
        methods[row["adjustment"]] = methods.get(row["adjustment"], 0) + 1
    result = {
        "schema_version": "2.0", "method": "EXPANDING_MULTI_WINDOW_WALK_FORWARD",
        "source_file": str(source) if source else None,
        "real_api_records": bool(payload.get("real_api_records")) and int(payload.get("synthetic_values", 0) or 0) == 0,
        "synthetic_values": int(payload.get("synthetic_values", 0) or 0),
        "verification_status": "NOT_VERIFIED", "raw_rows": len(clean),
        "window_count": len(windows), "positive_delta_windows": positive,
        "positive_delta_fraction": (positive / len(deltas)) if deltas else None,
        "adjustment_methods": methods, "windows": windows,
        "minimum_requirements_for_verified_claim": {
            "minimum_windows": 4, "minimum_positive_window_fraction": 0.75,
            "requires_real_api_records": True, "requires_zero_synthetic_values": True,
            "requires_sufficient_test_rows_each_window": True,
            "requires_independent_validation_before_live_signal": True,
        },
        "note": "No predictive or trading claim is made. Costs are subtracted only when explicitly present in source data; no liquidity cost is invented.",
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
