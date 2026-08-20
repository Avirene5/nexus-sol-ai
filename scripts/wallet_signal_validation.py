"""Walk-forward wallet signal validation from real historical datasets only.

No synthetic values, no random train/test split, and no score is marked verified.
The validator consumes token/wallet outcome rows produced by upstream collectors.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any

DATA = Path("data")
OUT = DATA / "wallet_signal_validation.json"


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"real_api_records": False, "synthetic_values": 0, "rows": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"real_api_records": False, "synthetic_values": 0, "rows": []}


def finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def get_time(row: dict[str, Any]) -> float | None:
    for k in ("signal_ts", "signal_time", "first_trade_ts", "timestamp", "ts"):
        if finite(row.get(k)):
            return float(row[k])
    return None


def get_forward_return(row: dict[str, Any]) -> float | None:
    for k in ("forward_return", "forward_return_pct", "return_pct", "outcome_return"):
        if finite(row.get(k)):
            return float(row[k])
    return None


def wallet(row: dict[str, Any]) -> str | None:
    return row.get("wallet") or row.get("wallet_address") or row.get("trader")


def main() -> None:
    # Accept the existing forward dataset under either current name.
    candidates = [
        DATA / "wallet_forward_performance.json",
        DATA / "forward_performance.json",
        DATA / "wallet_evidence.json",
    ]
    source = next((p for p in candidates if p.exists()), None)
    payload = load(source) if source else {"real_api_records": False, "synthetic_values": 0, "rows": []}
    rows = payload.get("rows", []) or payload.get("candidates", [])

    clean = []
    for r in rows:
        w = wallet(r)
        t = get_time(r)
        y = get_forward_return(r)
        if w and t is not None and y is not None and finite(y):
            clean.append({"wallet": w, "ts": t, "forward_return": y})

    # Deterministic time split. We never use a random split for financial data.
    clean.sort(key=lambda r: (r["ts"], r["wallet"]))
    n = len(clean)
    split = int(n * 0.70) if n >= 10 else 0
    train = clean[:split]
    test = clean[split:]

    train_by_wallet: dict[str, list[float]] = {}
    for r in train:
        train_by_wallet.setdefault(r["wallet"], []).append(r["forward_return"])

    ranked = sorted(
        ((w, mean(v), len(v)) for w, v in train_by_wallet.items() if len(v) >= 2),
        key=lambda x: (-x[1], -x[2], x[0]),
    )
    top = {w for w, _, _ in ranked[: max(1, min(20, len(ranked)))]}

    selected = [r["forward_return"] for r in test if r["wallet"] in top]
    control = [r["forward_return"] for r in test if r["wallet"] not in top]

    result = {
        "schema_version": "1.0",
        "method": "DETERMINISTIC_TIME_SPLIT_WALK_FORWARD",
        "source_file": str(source) if source else None,
        "real_api_records": bool(payload.get("real_api_records")) and int(payload.get("synthetic_values", 0) or 0) == 0,
        "synthetic_values": int(payload.get("synthetic_values", 0) or 0),
        "verification_status": "NOT_VERIFIED",
        "selection_rule": "top_train_mean_forward_return_with_min_2_observations",
        "train_rows": len(train),
        "test_rows": len(test),
        "selected_test_rows": len(selected),
        "control_test_rows": len(control),
        "selected_test_mean": mean(selected) if selected else None,
        "control_test_mean": mean(control) if control else None,
        "selected_test_median": median(selected) if selected else None,
        "control_test_median": median(control) if control else None,
        "delta_mean": (mean(selected) - mean(control)) if selected and control else None,
        "top_wallets": [
            {"wallet": w, "train_mean": m, "train_observations": c}
            for w, m, c in ranked[:20]
        ],
        "note": "No predictive claim is made until a sufficiently large real test set exists and multiple walk-forward windows confirm persistence.",
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
