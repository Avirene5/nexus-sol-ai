#!/usr/bin/env python3
"""Measure future token returns for observed wallet/token entries.

Only real Birdeye historical prices are used. Missing or immature checkpoints
remain missing; no synthetic values are created and nothing is VERIFIED here.
"""
from __future__ import annotations
import json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://public-api.birdeye.so"
KEY = os.environ.get("BIRDEYE_API_KEY", "").strip()
INPUT = Path("data/wallet_discovery_multisource.json")
OUTPUT = Path("data/wallet_forward_performance.json")
MAX_EVENTS = 25
HORIZONS = {"15m":900,"1h":3600,"6h":21600,"24h":86400,"7d":604800}

def api(params):
    if not KEY: raise RuntimeError("BIRDEYE_API_KEY is required")
    url = BASE + "/defi/historical_price_unix?" + urlencode(params)
    req = Request(url, headers={"X-API-KEY":KEY,"x-chain":"solana"})
    with urlopen(req, timeout=30) as r:
        if r.status != 200: raise RuntimeError(f"HTTP {r.status}")
        x=json.load(r)
    if not isinstance(x,dict): raise RuntimeError("Non-object Birdeye response")
    return x

def price(x):
    d=x.get("data")
    vals=[]
    if isinstance(d,dict): vals += [d.get("value"),d.get("price"),d.get("close"),d.get("c")]
    vals += [x.get("value"),x.get("price")]
    for v in vals:
        try:
            v=float(v)
            if v>0:return v
        except (TypeError,ValueError): pass
    return None

def ts(row):
    for k in ("firstTradeUnixTime","first_trade_unix_time","firstTradeTime","first_trade_time"):
        try:
            v=int(float(row.get(k)))
            if v>1_000_000_000:return v
        except (TypeError,ValueError): pass
    return None

def events(d):
    out=[]; seen=set()
    for c in d.get("candidates",[]):
        w=c.get("wallet")
        for token,records in (c.get("tokens") or {}).items():
            for r in records if isinstance(records,list) else []:
                if not isinstance(r,dict):continue
                t=ts(r)
                key=(w,token,t)
                if w and t and key not in seen:
                    seen.add(key); out.append({"wallet":w,"token":token,"entry_unix_time":t})
                    if len(out)>=MAX_EVENTS:return out
    return out

def main():
    if not INPUT.exists():raise RuntimeError("Missing discovery dataset")
    d=json.loads(INPUT.read_text(encoding="utf-8"))
    if d.get("real_api_records") is not True:raise RuntimeError("Input is not real API data")
    ev=events(d); now=int(datetime.now(timezone.utc).timestamp()); rows=[]; failures=0; matured=0; immature=0
    for e in ev:
        p0=price(api({"address":e["token"],"address_type":"token","unixtime":e["entry_unix_time"]}))
        row={**e,"entry_price_usd":p0,"checkpoints":{}}
        if p0 is None:
            row["status"]="ENTRY_PRICE_UNAVAILABLE"; failures+=1; rows.append(row); continue
        for label,delta in HORIZONS.items():
            cp=e["entry_unix_time"]+delta
            if cp>now:
                row["checkpoints"][label]={"status":"NOT_MATURED","checkpoint_unix_time":cp}; immature+=1; continue
            p=price(api({"address":e["token"],"address_type":"token","unixtime":cp}))
            if p is None:
                row["checkpoints"][label]={"status":"PRICE_UNAVAILABLE","checkpoint_unix_time":cp}; failures+=1
            else:
                row["checkpoints"][label]={"status":"OBSERVED","checkpoint_unix_time":cp,"price_usd":p,"return_pct":(p/p0-1)*100}; matured+=1
            time.sleep(.05)
        row["status"]="OBSERVED"; rows.append(row); time.sleep(.05)
    summary={}
    for r in rows:
        if r.get("status")!="OBSERVED":continue
        w=r["wallet"]; s=summary.setdefault(w,{"wallet":w,"event_count":0,"horizons":{}}); s["event_count"]+=1
        for h in HORIZONS:
            cp=r["checkpoints"].get(h,{})
            if cp.get("status")=="OBSERVED":
                a=s["horizons"].setdefault(h,[]); a.append(cp["return_pct"])
    wallet_summary=[]
    for w,s in sorted(summary.items()):
        hs={}
        for h,a in s["horizons"].items():
            a=sorted(a); hs[h]={"matured_events":len(a),"mean_return_pct":sum(a)/len(a),"positive_rate":sum(x>0 for x in a)/len(a),"median_return_pct":a[len(a)//2]}
        wallet_summary.append({"wallet":w,"event_count":s["event_count"],"horizons":hs})
    out={"schema_version":1,"mode":"FORWARD_PERFORMANCE_RESEARCH","chain":"solana","real_api_records":True,"source":"birdeye","source_endpoint":"/defi/historical_price_unix","generated_at":datetime.now(timezone.utc).isoformat(),"input_discovery_timestamp":d.get("completed_at"),"events_selected":len(ev),"matured_checkpoints":matured,"immature_checkpoints":immature,"price_failures":failures,"synthetic_values":0,"rows":rows,"wallet_summary":wallet_summary,"note":"Forward token returns are research evidence only; no VERIFIED label or realized wallet PnL is inferred."}
    OUTPUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"REAL_FORWARD_PERFORMANCE events={len(ev)} wallets={len(wallet_summary)} matured={matured} failures={failures} synthetic=0")

if __name__=="__main__":
    try: main()
    except Exception as e: print(f"FORWARD_PERFORMANCE_ERROR: {e}",file=sys.stderr); raise
