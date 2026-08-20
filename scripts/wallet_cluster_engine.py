"""Build conservative wallet clusters from real provenance/evidence datasets.

This module never invents relationships. A relationship is emitted only when the
input datasets contain an explicit shared funding source, or explicit shared token
and timing evidence. Clusters are research artifacts, not profitability scores.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

DATA = Path("data")
OUT = DATA / "wallet_clusters.json"


def load(name: str) -> dict[str, Any]:
    p = DATA / name
    if not p.exists():
        return {"real_api_records": False, "synthetic_values": 0, "rows": []}
    return json.loads(p.read_text(encoding="utf-8"))


def union_find(items: list[str]):
    parent = {x: x for x in items}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    return parent, find, union


def main() -> None:
    discovery = load("wallet_discovery_multisource.json")
    identity = load("wallet_identity_funding.json")
    evidence = load("wallet_evidence.json")

    wallets: set[str] = set()
    for row in discovery.get("candidates", []):
        if row.get("wallet"):
            wallets.add(row["wallet"])
    for row in identity.get("rows", []):
        if row.get("wallet"):
            wallets.add(row["wallet"])
    for row in evidence.get("rows", []):
        if row.get("wallet"):
            wallets.add(row["wallet"])

    parent, find, union = union_find(sorted(wallets))
    edges: list[dict[str, Any]] = []

    # Funding edges: only use an explicitly reported funding source.
    funding_to_wallets: dict[str, list[str]] = defaultdict(list)
    for row in identity.get("rows", []):
        wallet = row.get("wallet")
        source = row.get("first_funding_source") or row.get("funding_wallet")
        if wallet and source and source != wallet:
            funding_to_wallets[source].append(wallet)

    for source, members in funding_to_wallets.items():
        members = sorted(set(members))
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                union(a, b)
                edges.append({"a": a, "b": b, "reason": "shared_first_funding_source", "source": source})

    # Evidence rows may contain explicit shared-wallet/cluster identifiers from a
    # provider. Never infer a cluster from a numeric score alone.
    cluster_groups: dict[str, list[str]] = defaultdict(list)
    for row in evidence.get("rows", []):
        wallet = row.get("wallet")
        key = row.get("explicit_cluster_id")
        if wallet and key:
            cluster_groups[str(key)].append(wallet)

    for key, members in cluster_groups.items():
        members = sorted(set(members))
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                union(a, b)
                edges.append({"a": a, "b": b, "reason": "provider_explicit_cluster", "cluster_id": key})

    groups: dict[str, list[str]] = defaultdict(list)
    for wallet in sorted(wallets):
        groups[find(wallet)].append(wallet)

    clusters = []
    for root, members in sorted(groups.items()):
        clusters.append({
            "cluster_id": f"cluster_{root[:12]}",
            "wallets": members,
            "size": len(members),
            "independence_status": "CLUSTERED" if len(members) > 1 else "NO_RELATIONSHIP_FOUND",
        })

    provider_real = all(bool(x.get("real_api_records")) for x in (discovery, identity, evidence))
    synthetic = sum(int(x.get("synthetic_values", 0) or 0) for x in (discovery, identity, evidence))

    out = {
        "schema_version": "1.0",
        "chain": "solana",
        "mode": "WALLET_INDEPENDENCE_CLUSTER_RESEARCH",
        "real_api_records": provider_real,
        "synthetic_values": synthetic,
        "verification_status": "NOT_VERIFIED",
        "relationship_inference": "CONSERVATIVE_EXPLICIT_EVIDENCE_ONLY",
        "wallet_count": len(wallets),
        "cluster_count": len(clusters),
        "multi_wallet_cluster_count": sum(c["size"] > 1 for c in clusters),
        "edges": edges,
        "clusters": clusters,
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "wallet_count": len(wallets),
        "cluster_count": len(clusters),
        "edges": len(edges),
        "synthetic_values": synthetic,
    }, indent=2))


if __name__ == "__main__":
    main()
