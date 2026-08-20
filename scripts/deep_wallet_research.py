#!/usr/bin/env python3
"""Nexus SOL AI — deep wallet research from real discovery records.

This stage enriches discovered Solana wallets using real provider endpoints.
It deliberately does NOT assign a profitability/predictive score and does NOT
invent records when a provider is unavailable. It produces evidence for the
next transaction-level validation stage.
"""
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE="https://public-api.birdeye.so"
KEY=os.environ.get("BIRDEYE_API_KEY","").strip()
INPUT=Path("data/wallet_discovery_multisource.json")
OUTPUT=Path("data/wallet_deep_research.json")
MAX_WALLETS=50

def get(path, params):
    if not KEY: raise RuntimeError("BIRDEYE_API_KEY is required")
    u=BASE+path+"?"+urlencode(params,doseq=True)
    r=Request(u,headers={"X-API-KEY":KEY,"x-chain":"solana"})
    with urlopen(r,timeout=30) as h:
        if h.status!=200: raise RuntimeError(f"HTTP {h.status} {path}")
        x=json.load(h)
    if not isinstance(x,dict): raise RuntimeError("Invalid provider response")
    return x

def list_rows(x):
    d=x.get("data")
    if isinstance(d,dict):
        for k in ("items","list","traders","wallets","tokens"):
            if isinstance(d.get(k),list): return d[k]
    if isinstance(d,list): return d
    for k in ("items","traders","wallets"):
        if isinstance(x.get(k),list): return x[k]
    return []

def main():
    d=json.loads(INPUT.read_text(encoding="utf-8"))
    if d.get("real_api_records") is not True: raise RuntimeError("Discovery input is not real API data")
    wallets=d.get("candidates",[])[:MAX_WALLETS]
    rows=[]; failures=0
    for c in wallets:
        w=c.get("wallet")
        if not w: continue
        item={"wallet":w,"discovered_sources":c.get("sources",[]),"observed_at":datetime.now(timezone.utc).isoformat()}
        try:
            item["pnl_summary"]={"endpoint":"/wallet/v2/pnl/summary","data":get("/wallet/v2/pnl/summary",{"wallet":w,"duration":"90d","position_scope":"duration_only"}).get("data")}
        except Exception as e:
            item["pnl_summary"]={"status":"UNAVAILABLE","error":str(e)}; failures+=1
        try:
            item["pnl_details"]={"endpoint":"/wallet/v2/pnl/details","data":get("/wallet/v2/pnl/details",{"wallet":w,"duration":"90d","position_scope":"duration_only","sort_by":"realized_pnl","sort_type":"desc","offset":0,"limit":100}).get("data")}
        except Exception as e:
            item["pnl_details"]={"status":"UNAVAILABLE","error":str(e)}; failures+=1
        try:
            item["first_funded"]={"endpoint":"/wallet/v2/tx/first-funded","status":"REQUEST_ENDPOINT_AVAILABLE"}
        except Exception as e:
            item["first_funded"]={"status":"UNAVAILABLE","error":str(e)}
        item["verification_status"]="NOT_VERIFIED"
        item["predictive_score"]="NOT_CALCULATED"
        rows.append(item); time.sleep(.05)
    out={"schema_version":1,"mode":"DEEP_WALLET_RESEARCH","chain":"solana","real_api_records":True,"generated_at":datetime.now(timezone.utc).isoformat(),"wallets_requested":len(wallets),"wallets_researched":len(rows),"provider_failures":failures,"synthetic_values":0,"verification_status":"NOT_VERIFIED","rows":rows,"note":"Provider PnL is evidence, not proof of predictive ability. Transaction-level verification and forward performance remain separate stages."}
    OUTPUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"REAL_DEEP_WALLET_RESEARCH wallets={len(rows)} failures={failures} synthetic=0")

if __name__=="__main__": main()
