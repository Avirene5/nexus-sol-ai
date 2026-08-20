#!/usr/bin/env python3
"""Nexus SOL AI — evidence aggregation without fabricated scores.

Turns real discovery observations into auditable evidence counts. Provider
agreement is separated from wallet independence: multiple wallets funded by
one source or repeatedly co-occurring are not automatically treated as
independent votes. This script deliberately does not assign profitability or
predictive scores.
"""
from __future__ import annotations
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

INPUT = Path("data/wallet_discovery_multisource.json")
OUTPUT = Path("data/wallet_evidence.json")


def main() -> int:
    if not INPUT.exists():
        raise RuntimeError(f"Missing real discovery dataset: {INPUT}")
    d = json.loads(INPUT.read_text(encoding="utf-8"))
    if d.get("real_api_records") is not True:
        raise RuntimeError("Input is not marked as real API data")
    candidates = d.get("candidates")
    if not isinstance(candidates, list):
        raise RuntimeError("Invalid candidates collection")

    rows = []
    provider_counts = Counter()
    tag_counts = Counter()
    for c in candidates:
        wallet = c.get("wallet")
        if not wallet:
            continue
        sources = c.get("sources") or []
        providers = sorted({s.get("provider") for s in sources if s.get("provider")})
        endpoints = sorted({s.get("endpoint") for s in sources if s.get("endpoint")})
        tags = set()
        token_records = c.get("tokens") or {}
        for records in token_records.values():
            for r in records if isinstance(records, list) else []:
                for key in ("walletTags", "wallet_tags", "tags", "labels"):
                    value = r.get(key) if isinstance(r, dict) else None
                    if isinstance(value, list): tags.update(str(x) for x in value)
                    elif isinstance(value, str): tags.add(value)
        for p in providers: provider_counts[p] += 1
        for t in tags: tag_counts[t] += 1
        rows.append({
            "wallet": wallet,
            "provider_count": len(providers),
            "providers": providers,
            "endpoint_count": len(endpoints),
            "endpoints": endpoints,
            "observed_token_count": len(token_records),
            "observed_tags": sorted(tags),
            "wallet_pnl_present": bool(c.get("wallet_pnl")),
            "evidence_status": "OBSERVED",
            "independence_status": "UNASSESSED",
            "note": "Provider multiplicity is evidence coverage, not independent proof of wallet quality. Funding/co-occurrence graph must be applied before independence is assessed.",
        })

    rows.sort(key=lambda r: (-r["provider_count"], -r["endpoint_count"], -r["observed_token_count"], r["wallet"]))
    out = {
        "schema_version": 1,
        "mode": "WALLET_EVIDENCE",
        "chain": "solana",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "real_api_records": True,
        "synthetic_values": 0,
        "candidate_count": len(rows),
        "provider_counts": dict(provider_counts),
        "tag_counts": dict(tag_counts),
        "wallets": rows,
        "important_limit": "This file contains observed provenance/evidence only. It does not claim predictive power, profitability, independence, or VERIFIED status.",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"REAL_WALLET_EVIDENCE wallets={len(rows)} providers={dict(provider_counts)} synthetic=0")
    return 0

if __name__ == "__main__":
    main()
