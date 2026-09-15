# mail-enrichment

Tools that sit between the metadata index and the document catalog. The
authoritative plan is
[`docs/plans/00-mail-and-documents-MASTER.md`](../../../docs/plans/00-mail-and-documents-MASTER.md).
Stdlib only, same idiom as `mail-context`.

Every tool here imports `mailctx`, so it needs `mail-context` on `PYTHONPATH`:

```bash
export PYTHONPATH=/path/to/layers/openclaw/mail-context
python3 -m unittest discover -s tests      # 33 tests
```

## mailharvest.py — the bulk attachment harvest

The generalisation of `mailpilot.py` (which fetched 23 messages from a hand-
written TSV and had no memory). Reads a fetch list from
`mailctx.selectrun` and brings back bodies and attachments.

```bash
python3 mailharvest.py --ids /tmp/harvest-candidates.tsv --out /tmp/mailharvest \
        --workers 4 --rate 10
python3 mailharvest.py --ids ... --out ... --dry-run    # no API call
```

Properties, each of which exists because of a specific way a long run fails:

- **Resumable.** State is committed per message to
  `<out>/harvest-state.sqlite`. A run killed at message 900 of 3,000 restarts
  and does the remaining 2,100. Re-running a finished harvest fetches nothing.
- **Two retry budgets.** `--max-attempts` is how hard to try inside one run,
  against something that clears in seconds (Gmail reports per-user rate
  limiting as **HTTP 403 with reason `rateLimitExceeded`**, not 429 —
  `mailctx.gmail` already classifies it). `--max-runs` is how many
  human-launched runs may fail a message before giving up. Backoff is
  exponential and jittered, because without jitter N workers that hit the same
  limit wake together and hit it again together. After a burst clears, the
  pacing recovers toward what the caller asked for.
- **Aborts instead of burning the backlog.** `auth` and `forbidden` are not
  properties of a message — every remaining one would fail identically. The
  run stops and records *nothing* for the message that tripped it, so the
  untouched backlog stays retryable. Exit code 2.
- **Bounded concurrency.** One transport per worker thread, one shared token
  bucket. Raising `--workers` raises parallelism, never the request rate.
- **Chrome skipped before the request.** `format=full` reports `body.size` on
  every part, so a logo is skipped without spending an `attachments.get`.
  ~80% of pilot attachments by count were decorative.
- **Accountable.** `manifest.jsonl` (one row per message, with per-attachment
  sha256), `manifest.json` (run summary), `harvest.log`.

Read-only throughout: `messages.get(format=full)` and
`messages.attachments.get`, both already on the transport allowlist.
`tests/test_mailharvest.py` asserts the absence of a send path here too.

**Output is plaintext PII.** Import it into the life-index catalog, then
shred it.

## qcbench — cross-lane quality control

Benchmarks one model lane's classifications against a blind review by another
lane (typically: local model subject, cloud model reviewer).

```
python3 qcbench.py sample --db <snapshot.sqlite> --senders-csv <lane0 senders.csv> \
    --n 300 --out qc-batch-001.jsonl
# ... run each lane over the batch (see contract below) ...
python3 qcbench.py compare --subject-results local.jsonl \
    --reviewer-results cloud.jsonl --out qc-report.json
```

Properties:

- **Blind review.** The reviewer re-classifies independently; it never sees
  the subject lane's answer. Agreement stats stay unbiased. Judge-mode
  adjudication is a later step applied only to the disagreement queue.
- **Stratified sampling** across lane-0 classes (incl. `unknown`), fixed seed,
  reproducible. Marketing-class mail is excluded from batches by default so a
  cloud-bound batch respects the privacy gradient; `--include-marketing`
  overrides deliberately.
- **The batch file IS the egress list.** What a cloud reviewer will see is
  exactly the JSONL, reviewable before anything is sent.
- **Calibration, not just accuracy.** The report buckets subject-lane
  confidence against reviewer agreement — a local model that is wrong
  confidently is worse than one that is wrong honestly, because confidence
  drives escalation.
- Invalid JSON from a lane is counted as disagreement, never retried silently.

### Lane runner contract

A runner takes the batch JSONL and writes answers JSONL:
`{"item_id": ..., "class": <one of the 5>, "confidence": 0-1, "reason": ...}`.
The prompt is embedded in the batch header line. Runners are external:

- local lanes: `local_runner.py` — starts a private loopback `llama-server`
  from the caio-side pinned runtime against the shared weights in
  `/srv/models`, runs the batch, writes answers, stops the server.
- cloud lanes: via the OpenClaw `quick`/`work` aliases, or any API client.

### Interpreting a report

Reviewer agreement is a *proxy*, not ground truth — reviewer and subject can
share blind spots. The guard: operator spot-checks a small sample where both
lanes AGREE (correlated-error check), plus the whole disagreement queue.
Thresholds (initial, revisable): overall agreement ≥ 0.85 for a lane to own a
workload; any class below 0.75 stays escalated regardless of overall score.
