![ShadowOS — a personal assistant, built around my needs.](assets/brand/readme-banner.svg)

# ShadowOS

My evolving personal assistant for organizing information, working through
ideas and building solutions around everyday needs. Built inside
[OpenClaw](https://github.com/openclaw/openclaw), through WhatsApp and Telegram.

A voice note, photo, video, link or message is the starting point.
The aim: **easy input, useful context, less jumping between tools.**

I'm **Caio Tormin**. I build this with coding agents, use it daily, and keep
extending it for myself, friends and family. Personal project; no public signup yet.

[Case study](docs/CASE-STUDY.md) · [Architecture](docs/ARCHITECTURE.md) · [Code & setup](tools/README.md)

## A few things it does today

*Illustrative conversations with fictional details.*

### Groceries · video + voice

> **You · filming the fridge**<br>
> Coloca esse iogurte, o leite e os ovos no Costco.
> Se não tiver esse iogurte, pode ser o natural.
>
> **Shadow**<br>
> Adicionei os três. Anotei iogurte natural como alternativa.

Shared lists are in use by three invited people.
[Full conversation](docs/CHAT-EXAMPLES.md#groceries-a-video-becomes-a-shared-list) · [Illustration](assets/brand/conversation-grocery.svg)

### Doctor · preferences in context

> **You**<br>
> Find a dermatologist within 10 miles of 10001.
> Weekday mornings. My plan is ExamplePlan.
>
> **Shadow**<br>
> Dermatology, 10 miles, weekday mornings, ExamplePlan. Shall I search?

Early testing. The person confirms the search and approves outreach; no appointment booking.
[Full conversation](docs/CHAT-EXAMPLES.md#doctor-intake-without-repeating-what-was-already-said) · [Illustration](assets/brand/conversation-doctor.svg)

### Personal records · an answer with its source

> **You**<br>
> How much did I pay for the last HVAC repair?
>
> **Shadow**<br>
> The latest matching receipt I found is **$480**, dated June 12, 2026.
> It's marked paid. Source: “Example HVAC — repair receipt.”

My personal agent also handled a defective-product support request from a
photo: found the purchase and support contact, extracted the serial number,
sent the details and followed up when support replied.
[Full conversation & tool boundaries](docs/CHAT-EXAMPLES.md#personal-records-find-the-source-behind-the-answer) · [Illustration](assets/brand/conversation-life-index.svg)

## Why I'm building it

During stressful periods, finding forms, invoices and medical information
across apps kept breaking my flow. Even groceries meant searching a WhatsApp
thread while shopping with a child.

I wanted to capture things in the moment and find them when needed—without
maintaining another app or reconstructing the context each time.
[The story and design decisions →](docs/CASE-STUDY.md)

## Under the hood

OpenClaw provides the gateway, agent runtime and plugin ecosystem. My custom
work covers conversational workflows, shared state, retrieval, media handling
and access checks.

**Personal Shadow · Telegram:** broader configured tools, including Firecrawl
for research and calendar capture. **Invited Shadow · WhatsApp:** Grocery
and Doctor tools only. Personal record services remain read-only.

### Stack & workstation

September 2026 snapshot; version labels distinguish host, review and lockfile.

| Technology | Version / source |
|---|---|
| OpenClaw · Node.js | 2026.9.4 · 24.19.0 — documented host |
| Python · SQLite | 3.12.3 · 3.45.1 — local review |
| TypeScript · Vitest | 5.9.3 · 3.2.7 — Grocery lockfile |
| llama.cpp | b10604 — benchmark runner |
| whisper.cpp · GStreamer | Exact builds not recorded |

**EliteMini workstation:** Ryzen 7 8745H, Radeon 780M, ~29 GiB usable RAM,
1 TB NVMe, Ubuntu 24.04.4 LTS. Isolated OpenClaw account, loopback gateway,
systemd user services, SQLite, cloud models and local media processing.
[Versions and configuration →](docs/ARCHITECTURE.md#technology-and-version-snapshot)

### What I've learned

- Short replies and corrections need as much care as the first request.
- Models interpret intent; code checks identity, access and approval.
- Real conversations expose failures that isolated tests miss.

[Decisions, experiments and lessons →](docs/CASE-STUDY.md#what-real-use-changed)

## Status & contributing

The export review recorded **1,174 passing cases and 12 skips**. Live workflows
and TypeScript suites were not rerun. Calendar ordering and concurrent Doctor
sends have known failures; the workstation disk is plaintext.
[Validation](docs/VALIDATION.md) · [Security](docs/SECURITY.md)

Browse the [tools and setup notes](tools/README.md),
[contribution guide](CONTRIBUTING.md) or [documentation index](docs/README.md).
Code and synthetic fixtures are [MIT licensed](LICENSE); personal data stays
outside the export.
