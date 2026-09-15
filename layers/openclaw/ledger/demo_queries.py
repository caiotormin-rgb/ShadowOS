#!/usr/bin/env python3
"""The four retrievals the PRD calls success, run against the built ledger."""
from __future__ import annotations
import sqlite3, sys

DB = sys.argv[1] if len(sys.argv) > 1 else "outputs/ledger.sqlite"
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

QUERIES = [
 ('Q1  "what did I pay Acme Lawn"',
  """SELECT date, kind, CASE WHEN amount IS NULL THEN '(not in snippet)'
            ELSE printf('$%.2f',amount) END AS amount,
            ref_number AS invoice, lane, substr(message_id,1,16) AS gmail_msg
     FROM ledger WHERE counterparty LIKE '%cme Lawn%' ORDER BY date"""),
 ('Q1b total paid to Acme Lawn',
  """SELECT count(*) AS rows_, sum(amount IS NOT NULL) AS with_amount,
            printf('$%.2f', sum(amount)) AS total, min(date) AS first, max(date) AS last
     FROM ledger WHERE counterparty LIKE '%cme Lawn%'"""),
 ('Q2  "what subscriptions am I on"  (charged in the last 12 months)',
  """SELECT counterparty, COALESCE(NULLIF(description,''),'(item not in snippet)') AS item,
            count(*) AS charges,
            CASE WHEN sum(amount) IS NULL THEN '(no amount)' ELSE printf('$%.2f',sum(amount)) END AS billed,
            max(date) AS last_charge
     FROM ledger WHERE kind='subscription' AND date >= date('now','-12 months')
     GROUP BY counterparty, item HAVING count(*) >= 2
     ORDER BY count(*) DESC, counterparty LIMIT 15"""),
 ('Q3  "what were the travel dates"  (stays and flights, most recent first)',
  """SELECT date, counterparty, service_dates, ref_number, entity
     FROM ledger WHERE kind='booking'
       AND (counterparty LIKE 'Flight%' OR service_dates LIKE '%-%' OR entity IN ('Airbnb','United News & Deals'))
       AND service_dates IS NOT NULL
     ORDER BY date DESC LIMIT 14"""),
 ('Q4  "when was that appointment"',
  """SELECT date, counterparty, service_dates, entity
     FROM ledger WHERE kind='appointment' ORDER BY date DESC LIMIT 10"""),
 ('Q5  the evidence behind one Amazon purchase (duplicate-event linking)',
  """SELECT event_id, date, kind, ref_number, link_basis, is_primary, substr(message_id,1,16) AS msg
     FROM ledger_evidence
     WHERE event_id = (SELECT event_id FROM ledger_evidence WHERE entity='Amazon.com'
                       GROUP BY event_id HAVING count(*)>=3
                       ORDER BY count(*) DESC LIMIT 1)
     ORDER BY is_primary DESC, date"""),
]

for title, sql in QUERIES:
    cur = db.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    w = [min(max([len(str(c))] + [len(str(r[i])) for r in rows] or [0]), 44)
         for i, c in enumerate(cols)]
    print("=" * 78); print(title); print("=" * 78)
    print("  ".join(str(c)[:w[i]].ljust(w[i]) for i, c in enumerate(cols)))
    print("  ".join("-" * w[i] for i in range(len(cols))))
    for r in rows:
        print("  ".join(str(x)[:w[i]].ljust(w[i]) for i, x in enumerate(r)))
    print(f"({len(rows)} rows)\n")
