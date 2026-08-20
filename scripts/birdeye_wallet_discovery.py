#!/usr/bin/env python3
"""Nexus SOL AI — multi-source Solana wallet discovery.

Real-data rule: this collector only persists records returned by configured
providers. Birdeye is the primary provider; Solana Tracker is an optional
independent cross-source provider. Missing credentials fail closed for that
provider and never create synthetic records. Discovery/evidence remain
separate from transaction-level verification.
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

BIRDEYE_BASE = "https://public-api.birdeye.so"
ST_BASE = "https://data.solanatracker.io"
OUT = Path("data/wallet_discovery_multisource.json")
BIRDEYE_KEY = os.environ.get("BIRDEYE_API_KEY", "").strip()
ST_KEY = os.environ.get("SOLANA_TRACKER_API_KEY", "").strip()
MAX_WALLET_PNL_CHECKS = 50
MAX_ST_TOP = 100


def http_get(base: str, path: str, params: dict[str, object], headers: dict[str, str]) -> dict:
    url = f"{base}{path}"
    if params:
        url += "?" + urlencode(params, doseq=True)
    req = Request(url, headers=headers)
    with urlopen(req, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for {path}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Non-object response for {path}")
    return payload


def be_get(path: str, params: dict[str, object]) -> dict:
    if not BIRDEYE_KEY:
        raise RuntimeError("BIRDEYE_API_KEY is missing")
    return http_get(BIRDEYE_BASE, path, params, {"X-API-KEY": BIRDEYE_KEY, "x-chain": "solana"})


def st_get(path: str, params: dict[str, object]) -> dict:
    if not ST_KEY:
        raise RuntimeError("SOLANA_TRACKER_API_KEY is missing")
    return http_get(ST_BASE, path, params, {"x-api-key": ST_KEY})


def extract_list(payload: dict) -> list[dict]:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("items", "list", "tokens", "traders", "wallets"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    for key in ("traders", "items", "wallets"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def wallet_from_item(item: dict) -> str | None:
    wallet = item.get("owner") or item.get("wallet") or item.get("address")
    return str(wallet) if wallet else None


def add_candidate(candidates: dict[str, dict], wallet: str, source: dict, observed: str) -> dict:
    existing = candidates.setdefault(
        wallet,
        {"wallet": wallet, "sources": [], "tokens": {}, "wallet_pnl": {}, "discovered_at": observed},
    )
    if source not in existing["sources"]:
        existing["sources"].append(source)
    return existing


def main() -> int:
    if not BIRDEYE_KEY:
        raise RuntimeError("BIRDEYE_API_KEY is required; no discovery run will be performed")

    started = datetime.now(timezone.utc).isoformat()
    observed = datetime.now(timezone.utc).isoformat()
    candidates: dict[str, dict] = {}
    provider_status = {"birdeye": "enabled", "solana_tracker": "enabled" if ST_KEY else "not_configured"}
    listings: list[dict] = []
    token_runs: list[dict] = []
    global_records = 0
    st_records = 0

    # ---------------- Birdeye ----------------
    listing_payload = be_get(
        "/defi/v2/tokens/new_listing",
        {"limit": 20, "meme_platform_enabled": "true"},
    )
    listings = extract_list(listing_payload)

    for ranking_type in ("1W", "30d", "90d"):
        payload = be_get(
            "/trader/gainers-losers",
            {"type": ranking_type, "sort_by": "realized_pnl", "sort_type": "desc", "offset": 0, "limit": 100},
        )
        for item in extract_list(payload):
            wallet = wallet_from_item(item)
            if wallet:
                add_candidate(candidates, wallet, {
                    "provider": "birdeye", "endpoint": "/trader/gainers-losers",
                    "ranking_type": ranking_type, "sort_by": "realized_pnl", "observed_at": observed,
                }, observed)
                global_records += 1
        time.sleep(0.05)

    for token in listings:
        address = token.get("address") or token.get("tokenAddress")
        if not address:
            continue
        trader_records = 0
        for sort_by in ("realized_pnl", "volume_usd"):
            payload = be_get(
                "/defi/v2/tokens/top_traders",
                {"address": address, "time_frame": "7d", "sort_type": "desc", "sort_by": sort_by,
                 "offset": 0, "limit": 10, "get_holders_networth": "true"},
            )
            for trader in extract_list(payload):
                wallet = wallet_from_item(trader)
                if not wallet:
                    continue
                existing = add_candidate(candidates, wallet, {
                    "provider": "birdeye", "endpoint": "/defi/v2/tokens/top_traders",
                    "sort_by": sort_by, "time_frame": "7d", "observed_at": observed,
                }, observed)
                existing["tokens"].setdefault(str(address), []).append(trader)
                trader_records += 1
            time.sleep(0.05)
        token_runs.append({"token": address, "trader_records": trader_records})

    # Wallet-level PnL is evidence only. Birdeye explicitly warns that protocol
    # trade data may not be fully backfilled, so this never becomes VERIFIED.
    pnl_checked = 0
    for wallet in list(candidates)[:MAX_WALLET_PNL_CHECKS]:
        payload = be_get("/wallet/v2/pnl/summary", {
            "wallet": wallet, "duration": "90d", "position_scope": "duration_only"
        })
        candidates[wallet]["wallet_pnl"] = {
            "provider": "birdeye", "endpoint": "/wallet/v2/pnl/summary", "duration": "90d",
            "position_scope": "duration_only", "observed_at": observed, "data": payload.get("data"),
        }
        pnl_checked += 1
        time.sleep(0.05)

    # ---------------- Solana Tracker (optional cross-source) ----------------
    # Uses documented PnL V2 top-trader and KOL endpoints. No scraping.
    if ST_KEY:
        for days in (7, 30, 90):
            payload = st_get("/v2/pnl/leaderboard/top", {"days": days, "limit": MAX_ST_TOP, "pnlMode": "adjusted"})
            for item in extract_list(payload):
                wallet = wallet_from_item(item)
                if wallet:
                    add_candidate(candidates, wallet, {
                        "provider": "solana_tracker", "endpoint": "/v2/pnl/leaderboard/top",
                        "days": days, "pnl_mode": "adjusted", "observed_at": observed,
                    }, observed)
                    st_records += 1
            time.sleep(0.05)
        payload = st_get("/v2/pnl/leaderboard/kols", {"sort": "total", "direction": "desc", "limit": 50})
        for item in extract_list(payload):
            wallet = wallet_from_item(item)
            if wallet:
                add_candidate(candidates, wallet, {
                    "provider": "solana_tracker", "endpoint": "/v2/pnl/leaderboard/kols",
                    "sort": "total", "direction": "desc", "observed_at": observed,
                }, observed)
                st_records += 1

    result = {
        "schema_version": 3,
        "mode": "DISCOVERY_ONLY",
        "chain": "solana",
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "real_api_records": True,
        "provider_status": provider_status,
        "candidate_wallet_count": len(candidates),
        "listing_count": len(listings),
        "birdeye_global_leaderboard_record_count": global_records,
        "birdeye_wallet_pnl_checks": pnl_checked,
        "solana_tracker_record_count": st_records,
        "token_runs": token_runs,
        "verification_status": "NOT_VERIFIED",
        "verification_note": "Discovery and provider PnL are evidence only. Transaction-level/on-chain verification is a separate stage.",
        "candidates": sorted(candidates.values(), key=lambda x: x["wallet"]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "REAL_MULTISOURCE_DISCOVERY "
        f"wallets={len(candidates)} listings={len(listings)} "
        f"birdeye_global={global_records} birdeye_pnl={pnl_checked} st={st_records}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"MULTISOURCE_DISCOVERY_ERROR: {exc}", file=sys.stderr)
        raise
