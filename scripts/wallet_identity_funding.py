#!/usr/bin/env python3
"""Nexus SOL AI — real wallet identity + funding provenance enrichment.

Uses Birdeye Wallet Identity and First Tx Funded APIs. Missing provider data is
kept as unavailable. No wallet score is calculated and no synthetic records are
created.
"""
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE="https://public-api.birdeye.so"
KEY=os.environ.get("BIRDEYE_API_KEY","").strip()
INPUT=Path("data/wallet_discovery_multisource.json")
OUTPUT=Path("data/wallet_identity_funding.json")
MAX_WALLETS=100

def request(path, method="GET", params=None, body=None):
    if not KEY: raise RuntimeError("BIRDEYE_API_KEY is required")
    url=BASE+path
    if params:
        from urllib.parse import urlencode
        url += "?"+urlencode(params,doseq=True)
    headers={"X-API-KEY":KEY,"x-chain":"solana","Content-Type":"application/json"}
    data=json.dumps(body).encode() if body is not None else None
    req=Request(url,headers=headers,method=method,data=data)
    with urlopen(req,timeout=30) as r:
        if r.status!=200: raise RuntimeError(f"HTTP {r.status} {path}")
        x=json.load(r)
    if not isinstance(x,dict): raise RuntimeError("Invalid provider response")
    return x

def wallets_from_discovery(d):
    seen=[]; used=set()
    for c in d.get("candidates",[]):
        w=c.get("wallet")
        if w and w not in used:
            used.add(w); seen.append(w)
        if len(seen)>=MAX_WALLETS: break
    return seen

def main():
    if not INPUT.exists(): raise RuntimeError("Missing wallet discovery dataset")
    d=json.loads(INPUT.read_text(encoding="utf-8"))
    if d.get("real_api_records") is not True: raise RuntimeError("Discovery input is not real API data")
    wallets=wallets_from_discovery(d)
    identity={}; funding={}; failures=0
    # Identity API supports up to 100 addresses per request.
    try:
        resp=request("/identity/v1/multiple",method="POST",body={"wallets":wallets})
        identity=resp.get("data") if isinstance(resp.get("data"),dict) else resp.get("data",{})
    except Exception as e:
        failures+=1; identity={"status":"UNAVAILABLE","error":str(e)}
    # First-funded supports up to 50 wallets per request.
    for i in range(0,len(wallets),50):
        batch=wallets[i:i+50]
        try:
            resp=request("/wallet/v2/tx/first-funded",method="POST",body={"wallets":batch})
            data=resp.get("data")
            if isinstance(data,dict): funding.update(data)
        except Exception as e:
            failures+=1
            for w in batch: funding[w]={"status":"UNAVAILABLE","error":str(e)}
        time.sleep(.1)
    rows=[]
    for w in wallets:
        rows.append({"wallet":w,"identity":identity.get(w) if isinstance(identity,dict) else None,"first_funded":funding.get(w),"observed_at":datetime.now(timezone.utc).isoformat()})
    out={"schema_version":1,"mode":"WALLET_IDENTITY_FUNDING_RESEARCH","chain":"solana","real_api_records":True,"source":"birdeye","generated_at":datetime.now(timezone.utc).isoformat(),"wallets_requested":len(wallets),"wallets_enriched":len(rows),"provider_failures":failures,"synthetic_values":0,"verification_status":"NOT_VERIFIED","rows":rows,"note":"Identity and funding are provenance evidence only; no profitability or predictive score is inferred."}
    OUTPUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"REAL_IDENTITY_FUNDING wallets={len(rows)} failures={failures} synthetic=0")

if __name__=="__main__": main()
