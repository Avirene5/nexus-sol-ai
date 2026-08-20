#!/usr/bin/env python3
"""Nexus SOL AI — real Birdeye wallet discovery.

This collector never invents records. It only persists data returned by
Birdeye, with provider/endpoint/timestamp provenance. Discovery and external
PnL evidence are deliberately kept separate from on-chain verification.
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
MAX_WALLET_PNL_CHECKS = 50


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
        for key in ("items", "list", "tokens", "traders", "wallets"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def wallet_from_trader(item: dict) -> str | None:
    wallet = item.get("owner") or item.get("wallet") or item.get("address")
    return str(wallet) if wallet else None


def add_candidate(candidates: dict[str, dict], wallet: str, source: dict) -> dict:
    existing = candidates.setdefault(
        wallet,
        {"wallet": wallet, "sources": [], "tokens": {}, "wallet_pnl": {}, "discovered_at": source["observed_at"]},
    )
    if source not in existing["sources"]:
        existing["sources"].append(source)
    return existing


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    candidates: dict[str, dict] = {}
    token_runs: list[dict] = []
    global_records = 0

    # Seed 1: fresh Solana listings. This is deliberately bounded and is NOT
    # represented as an exhaustive scan of Solana.
    listing_payload = api_get(
        "/defi/v2/tokens/new_listing",
        {"limit": 20, "meme_platform_enabled": "true"},
    )
    listings = extract_list(listing_payload)
    observed = datetime.now(timezone.utc).isoformat()

    # Seed 2: independent trader leaderboard source within Birdeye.
    # This catches successful traders even when they are not in the fresh-listing seed.
    for ranking_type in ("1W", "30d", "90d"):
        payload = api_get(
            "/trader/gainers-losers",
            {
                "type": ranking_type,
                "sort_by": "realized_pnl",
                "sort_type": "desc",
                "offset": 0,
                "limit": 100,
            },
        )
        for item in extract_list(payload):
            wallet = wallet_from_trader(item)
            if not wallet:
                continue
            add_candidate(
                candidates,
                wallet,
                {
                    "provider": "birdeye",
                    "endpoint": "/trader/gainers-losers",
                    "ranking_type": ranking_type,
                    "sort_by": "realized_pnl",
                    "observed_at": observed,
                },
            )
            global_records += 1
        time.sleep(0.05)

    # Token-level discovery: two independent rankings per newly listed token.
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
                wallet = wallet_from_trader(trader)
                if not wallet:
                    continue
                existing = add_candidate(
                    candidates,
                    wallet,
                    {
                        "provider": "birdeye",
                        "endpoint": "/defi/v2/tokens/top_traders",
                        "sort_by": sort_by,
                        "time_frame": "7d",
                        "observed_at": observed,
                    },
                )
                existing["tokens"].setdefault(str(address), []).append(trader)
                traders.append(trader)
            time.sleep(0.05)
        token_runs.append({"token": address, "trader_records": len(traders)})

    # Add a bounded wallet-level PnL evidence layer. This does NOT verify
    # transactions; Birdeye notes that protocol trade data may not be fully
    # backfilled, so the result remains external evidence only.
    pnl_checked = 0
    for wallet in list(candidates)[:MAX_WALLET_PNL_CHECKS]:
        payload = api_get(
            "/wallet/v2/pnl/summary",
            {"wallet": wallet, "duration": "90d", "position_scope": "duration_only"},
        )
        candidates[wallet]["wallet_pnl"] = {
            "provider": "birdeye",
            "endpoint": "/wallet/v2/pnl/summary",
            "duration": "90d",
            "position_scope": "duration_only",
            "observed_at": observed,
            "data": payload.get("data"),
        }
        pnl_checked += 1
        time.sleep(0.05)

    result = {
        "schema_version": 2,
        "mode": "DISCOVERY_ONLY",
        "provider": "Birdeye",
        "chain": "solana",
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "real_api_records": True,
        "candidate_wallet_count": len(candidates),
        "listing_count": len(listings),
        "global_leaderboard_record_count": global_records,
        "wallet_pnl_checks": pnl_checked,
        "token_runs": token_runs,
        "verification_status": "NOT_VERIFIED",
        "verification_note": "External Birdeye PnL is evidence only; transaction-level/on-chain verification is separate.",
        "candidates": sorted(candidates.values(), key=lambda x: x["wallet"]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "REAL_BIRDEYE_DISCOVERY "
        f"wallets={len(candidates)} listings={len(listings)} "
        f"global_records={global_records} pnl_checks={pnl_checked}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"BIRDEYE_DISCOVERY_ERROR: {exc}", file=sys.stderr)
        raise
