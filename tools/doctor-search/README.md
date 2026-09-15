# Doctor search

Does the legwork of finding a provider for a household member. It searches the
web, reads practice websites, checks each practice offers exactly what was
asked, whether it takes the plan, reviews and distance, and ranks them. It then
helps contact them. Used from the household WhatsApp bot (agent
`shared-tools`) through the `doctor-search-tool` plugin.

```
new -> intake ... -> confirm-intake  (queues a background search)
     -> run-jobs (timer, every 2 min): curate -> WhatsApp the shortlist to the requester
     -> show --format shortlist -> choose -> show --format summary -> close
email: verify-email -> confirm-email -> draft (draft + code go to the requester on WhatsApp)
     -> /ok CODE (plugin command -> cli ok) -> poll-notify (timer, every 5 min)
```

## Layout
- `core/`: stdlib Python engine and CLI (`cli.py`), with tests in `core/tests`.
  - `store.py`: SQLite step machine. Only the requester can see a request. Intake keeps the plan name only (no member ID, date of birth or diagnosis). Background jobs are queued, claimed and failed here.
  - `curate.py`: the background research:
    1. The model turns the request into English search phrases.
    2. Firecrawl search near the ZIP; directories, insurers, social and job sites are skipped.
    3. Each site's landing page plus up to 2 insurance/services/contact pages.
    4. The model extracts the facts and a 0–3 match with a quote; anything below 2 is dropped.
    5. Ratings come from search snippets; review sites are never scraped.
    6. Distance by ZIP; offices that can't be placed are dropped.
    7. Ranked, top 8 kept.
  - `web.py`: `openclaw infer web search|fetch --provider firecrawl`. The key stays in OpenClaw; results are cached for 7 days.
  - `llm.py`: `openclaw infer model run --agent shared-tools --local` (no tools), JSON answers.
  - `outreach.py`: email from AgentMail `shadow@agentmail.example` over SMTP/IMAP. The key file is `~/.config/household/agentmail.key` (0600).
    - Before sending: the requester's email is verified by code, and a changed draft is not sent.
    - Addresses: To must be on the practice's site domain; CC must be a verified household email.
    - Protections: STOP suppression; limits of 10 per request and 30 per day.
    - Replies: matched by `[REQ-id]` and wrapped as untrusted.
  - `notify.py`: WhatsApp messages to the requester via `openclaw message send --account tools`.
  - `render.py`: EN/PT text.
- `plugin/`: OpenClaw plugin with tool `doctor_search` (WhatsApp account `tools`, allowlisted senders only) and command `/ok CODE`. The only way a draft is approved; the model never sees the code.
- Timers (user systemd, sources in `~/tools/scripts/systemd/`):
  - `doctor-jobs` every 2 min: `run-jobs`
  - `doctor-mail` every 5 min: `poll-notify`
  - `doctor-retention` daily at 04:40: `purge` + `mail-purge`
- Retention: requests, candidates, mail rows and bot inbox mail are deleted 90 days after close (or 90 days untouched).

## Tests
```bash
cd core && python3 -m unittest discover -s tests
cd plugin && npm run build && npx vitest run
```

## Deploy
- First install: `~/tools/scripts/install-doctor-whatsapp.sh` (config and grants; run by the owner).
- Updates: `~/tools/scripts/update-doctor-v2.sh` (tests, agent instructions, timer, gateway restart).
- The agent section lives in `~/tools/scripts/doctor-agents-section.md` and is copied into `~/.openclaw/workspace-shared-tools/AGENTS.md`.

## Data (not in git)
- `core/data/2024_Gaz_zcta_national.txt`: Census 2024 Gazetteer ZCTA file (ZIP centroids).
- `core/data/doctor.sqlite3`: requests and cache (mode 0600).

## Known gaps
See `~/tools/playground/tasks.md` → "Pick up later": reviews need a Google Places key; contact-form filling and background outreach aren't built; the first live test favored health-system departments over private practices.
