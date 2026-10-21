#!/usr/bin/env python3
"""Compare Genesys Cloud call quality (MOS) between groups of agents, e.g.
thin-client pilot agents vs. agents still on laptops.

Pilot success criterion: "Call quality complaints no higher than on laptops".
Complaints are subjective; this gives the objective side from Genesys' own
per-conversation media statistics.

Needs a Genesys Cloud OAuth client (client credentials) with the
analytics:conversationDetail:view permission.

  GENESYS_CLIENT_ID=... GENESYS_CLIENT_SECRET=... \
  python3 tools/genesys_call_quality.py --region mypurecloud.ie \
      --agents agents.csv --from 2026-11-01 --to 2026-11-15

agents.csv:   group,user_id
              thin-client,6f1c...       (Genesys user ids)
              laptop,a83b...

NOTE: written against the public Analytics API documentation; it has not been
run against a live org as part of this lab. Try it on a short interval first.
"""
import argparse
import csv
import os
import statistics
import sys
from collections import defaultdict

import requests


def token(region, client_id, secret):
    r = requests.post(f"https://login.{region}/oauth/token",
                      data={"grant_type": "client_credentials"}, auth=(client_id, secret), timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def conversations(region, tok, user_ids, interval):
    url = f"https://api.{region}/api/v2/analytics/conversations/details/query"
    page = 1
    while True:
        body = {
            "interval": interval,
            "order": "asc",
            "orderBy": "conversationStart",
            "paging": {"pageSize": 100, "pageNumber": page},
            "segmentFilters": [{
                "type": "or",
                "predicates": [{"type": "dimension", "dimension": "userId", "operator": "matches", "value": u}
                               for u in user_ids],
            }],
        }
        r = requests.post(url, json=body, headers={"Authorization": f"Bearer {tok}"}, timeout=60)
        r.raise_for_status()
        batch = r.json().get("conversations", [])
        yield from batch
        if len(batch) < 100:
            return
        page += 1


def pct(values, p):
    values = sorted(values)
    return values[max(0, int(round(p / 100 * len(values))) - 1)] if values else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", required=True, help="e.g. mypurecloud.com, mypurecloud.ie, mypurecloud.de")
    ap.add_argument("--agents", required=True, help="CSV with columns group,user_id")
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--poor", type=float, default=3.5, help="MOS below this counts as a poor call")
    args = ap.parse_args()

    group_of = {}
    with open(args.agents, newline="") as f:
        for row in csv.DictReader(f):
            group_of[row["user_id"].strip()] = row["group"].strip()

    tok = token(args.region, os.environ["GENESYS_CLIENT_ID"], os.environ["GENESYS_CLIENT_SECRET"])
    interval = f"{args.start}T00:00:00Z/{args.end}T00:00:00Z"
    mos = defaultdict(list)
    ids = list(group_of)
    for i in range(0, len(ids), 50):            # keep each filter a reasonable size
        for conv in conversations(args.region, tok, ids[i:i + 50], interval):
            value = conv.get("mediaStatsMinConversationMos")
            if value is None:
                continue
            agents = {p.get("userId") for p in conv.get("participants", []) if p.get("purpose") == "agent"}
            for g in {group_of[a] for a in agents if a in group_of}:
                mos[g].append(value)

    print(f"{'group':<16}{'calls':>7}{'median MOS':>12}{'p10 MOS':>10}{'poor %':>9}")
    for g, vals in sorted(mos.items()):
        poor = 100 * sum(v < args.poor for v in vals) / len(vals)
        print(f"{g:<16}{len(vals):>7}{statistics.median(vals):>12.2f}{pct(vals, 10):>10.2f}{poor:>8.1f}%")
    if not mos:
        print("no calls with media statistics found", file=sys.stderr)


if __name__ == "__main__":
    main()
