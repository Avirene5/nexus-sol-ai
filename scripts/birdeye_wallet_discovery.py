#!/usr/bin/env python3
"""Nexus SOL AI — real Birdeye wallet discovery.

This collector never invents records. It only persists API responses that were
actually returned by Birdeye, with source/timestamp metadata. It discovers
recent Solana listings, then ranks their traders by realized/total PnL and
activity. Verification is deliberately separate from discovery.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://public-api.birdeye.so"
OUT = Path("data/wallet_discovery_birdeye.json")
API_KEY = os.environ.get("BIRDEYE_API_KEY", "").strip()


def api_get(path: str, params: dict[str, object]) -> dict:
    if not API_KEY:
        raise RuntimeError("BIRDEYE_API_KEY is missing; no discovery run was performed")
    url = f"{BASE}{path}?{urlencode(params, doseq=True)}"
    req = Request(url, headers={"X-API-KEY": API_KEY, "x-chain": "solana"})
    with urlopen(req, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"Birdeye HTTP {response.status} for {path}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Birdeye returned non-object payload for {path}")
    return payload


def extract_list(payload: dict) -> list[dict]:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("items", "list", "tokens", "traders"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    # Fresh-listing discovery is intentionally bounded: it is a seed source,
    # not a claim that all Solana tokens/wallets have been scanned.
    listing_payload = api_get(
        "/defi/v2/tokens/new_listing",
        {"limit": 20, "meme_platform_enabled": "true"},
    )
    listings = extract_list(listing_payload)

    candidates: dict[str, dict] = {}
    token_runs: list[dict] = []
    for token in listings:
        address = token.get("address") or token.get("tokenAddress")
        if not address:
            continue
        traders = []
        for sort_by in ("realized_pnl", "volume_usd"):
            payload = api_get(
                "/defi/v2/tokens/top_traders",
                {
                    "address": address,
                    "time_frame": "7d",
                    "sort_type": "desc",
                    "sort_by": sort_by,
                    "offset": 0,
                    "limit": 10,
                    "get_holders_networth": "true",
                },
            )
            for trader in extract_list(payload):
                wallet = trader.get("owner") or trader.get("wallet") or trader.get("address")
                if not wallet:
                    continue
                key = str(wallet)
                existing = candidates.setdefault(
                    key,
                    {
                        "wallet": key,
                        "sources": [],
                        "tokens": {},
                        "discovered_at": started,
                    },
                )
                source = {
                    "provider": "birdeye",
                    "endpoint": "/defi/v2/tokens/top_traders",
                    "sort_by": sort_by,
                    "time_frame": "7d",
                }
                if source not in existing["sources"]:
                    existing["sources"].append(source)
                existing["tokens"].setdefault(str(address), []).append(trader)
                traders.append(trader)
            time.sleep(0.05)
        token_runs.append({"token": address, "trader_records": len(traders)})

    result = {
        "schema_version": 1,
        "mode": "DISCOVERY_ONLY",
        "provider": "Birdeye",
        "chain": "solana",
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "real_api_records": True,
        "candidate_wallet_count": len(candidates),
        "listing_count": len(listings),
        "token_runs": token_runs,
        "verification_status": "NOT_VERIFIED",
        "candidates": sorted(candidates.values(), key=lambda x: x["wallet"]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"REAL_BIRDEYE_DISCOVERY wallets={len(candidates)} listings={len(listings)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"BIRDEYE_DISCOVERY_ERROR: {exc}", file=sys.stderr)
        raise
