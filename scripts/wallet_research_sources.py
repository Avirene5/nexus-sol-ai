#!/usr/bin/env python3
"""Nexus SOL AI — deterministic wallet research planner.

Builds a research queue from the real multisource discovery dataset. It does
not invent wallets and does not assign predictive scores. The queue prioritizes
wallets for deeper evidence collection using observed source diversity, token
count, and real tagged evidence.
"""
from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict

INPUT=Path("data/wallet_discovery_multisource.json")
OUTPUT=Path("data/wallet_research_queue.json")


def main():
    if not INPUT.exists(): raise RuntimeError(f"Missing {INPUT}")
    d=json.loads(INPUT.read_text(encoding="utf-8"))
    if d.get("real_api_records") is not True: raise RuntimeError("Discovery dataset is not marked real_api_records")
    rows=[]
    for c in d.get("candidates",[]):
        wallet=c.get("wallet")
        if not wallet: continue
        tokens=c.get("tokens") or {}
        sources=c.get("sources") or []
        tags=defaultdict(int)
        for s in sources:
            for tag in s.get("tags") or []: tags[str(tag)]+=1
        rows.append({
            "wallet":wallet,
            "token_count":len(tokens),
            "source_count":len({s.get("provider") for s in sources if s.get("provider")}),
            "source_providers":sorted({s.get("provider") for s in sources if s.get("provider")}),
            "observed_tags":dict(sorted(tags.items())),
            "research_priority":"HIGH" if len(tokens)>=3 and len({s.get("provider") for s in sources if s.get("provider")})>=2 else "STANDARD",
            "next_evidence":["wallet_pnl_summary","wallet_pnl_details","first_funding","token_top_traders","holder_positions"]
        })
    rows.sort(key=lambda r:(r["research_priority"]!="HIGH",-r["token_count"],-r["source_count"],r["wallet"]))
    out={"schema_version":1,"mode":"REAL_WALLET_RESEARCH_QUEUE","real_api_records":True,"generated_at":d.get("completed_at"),"candidate_count":len(rows),"rows":rows,"note":"Priority is a research ordering only. It is not a profitability or predictive score."}
    OUTPUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"REAL_WALLET_RESEARCH_QUEUE candidates={len(rows)}")

if __name__=="__main__": main()
