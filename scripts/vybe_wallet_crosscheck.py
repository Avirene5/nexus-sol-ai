#!/usr/bin/env python3
"""Nexus SOL AI — optional independent Vybe wallet cross-check.

Only persists data returned by Vybe. Missing credentials produce an explicit
NOT_CONFIGURED dataset with zero rows and zero synthetic values.
"""
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

KEY=os.environ.get("VYBE_API_KEY","").strip()
INPUT=Path("data/wallet_discovery_multisource.json")
OUTPUT=Path("data/wallet_vybe_crosscheck.json")
BASE="https://api.vybenetwork.xyz/v4"
MAX_TOKENS=20
LIMIT=100

def get(path, params):
    url=f"{BASE}{path}?{urlencode(params)}"
    req=Request(url,headers={"X-API-Key":KEY,"Accept":"application/json"})
    with urlopen(req,timeout=30) as r:
        if r.status!=200: raise RuntimeError(f"HTTP {r.status} {path}")
        x=json.load(r)
    if not isinstance(x,dict): raise RuntimeError("Invalid Vybe response")
    return x

def extract_list(payload):
    data=payload.get("data")
    if isinstance(data,list): return [x for x in data if isinstance(x,dict)]
    if isinstance(data,dict):
        for k in ("items","traders","results"):
            v=data.get(k)
            if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
    for k in ("items","traders","results"):
        v=payload.get(k)
        if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
    return []

def token_universe(d):
    seen=[]
    for c in d.get("candidates",[]):
        for token in (c.get("tokens") or {}):
            if token not in seen: seen.append(token)
    return seen[:MAX_TOKENS]

def main():
    now=datetime.now(timezone.utc).isoformat()
    if not INPUT.exists(): raise RuntimeError("Missing wallet discovery dataset")
    d=json.loads(INPUT.read_text(encoding="utf-8"))
    if d.get("real_api_records") is not True: raise RuntimeError("Discovery input is not real API data")
    if not KEY:
        out={"schema_version":1,"mode":"VYBE_CROSSCHECK","chain":"solana","real_api_records":False,"provider_status":"NOT_CONFIGURED","generated_at":now,"synthetic_values":0,"verification_status":"NOT_VERIFIED","rows":[],"note":"VYBE_API_KEY is not configured; no synthetic cross-source data was generated."}
        OUTPUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print("VYBE_CROSSCHECK NOT_CONFIGURED synthetic=0")
        return
    rows=[]; failures=0; tokens=token_universe(d)
    for mint in tokens:
        try:
            payload=get(f"/tokens/{mint}/top-pnl-traders",{"resolution":"7d","limit":LIMIT,"page":0,"sortByDesc":"realizedPnlUsd"})
            for t in extract_list(payload):
                wallet=t.get("traderAddress") or t.get("wallet") or t.get("owner")
                if wallet:
                    rows.append({"wallet":wallet,"token":mint,"provider":"vybe","endpoint":"/v4/tokens/{mintAddress}/top-pnl-traders","resolution":"7d","observed_at":now,"data":t})
        except Exception:
            failures+=1
        time.sleep(.05)
    out={"schema_version":1,"mode":"VYBE_CROSSCHECK","chain":"solana","real_api_records":True,"provider_status":"ENABLED","generated_at":now,"tokens_checked":len(tokens),"rows":rows,"provider_failures":failures,"synthetic_values":0,"verification_status":"NOT_VERIFIED","note":"Vybe is an independent discovery/cross-check source; rankings are evidence, not proof of future performance."}
    OUTPUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"REAL_VYBE_CROSSCHECK tokens={len(tokens)} rows={len(rows)} failures={failures} synthetic=0")

if __name__=="__main__": main()
