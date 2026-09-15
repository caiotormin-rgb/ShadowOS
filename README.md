![ShadowOS — Less to juggle. More room to think and get things done.](assets/brand/readme-banner.svg)

# ShadowOS

My evolving personal assistant for organizing information, working through
ideas and building solutions around everyday needs. Built inside
[OpenClaw](https://github.com/openclaw/openclaw), through WhatsApp and Telegram.

A voice note, photo, video, link or message is the starting point.
The aim: **easy input, useful context, less jumping between tools.**

I'm **Caio Tormin**. I build this with coding agents, use it daily, and keep
extending it for myself, friends and family. Personal project; no public signup yet.

[Examples](#in-conversation) · [How it's built](#how-its-built) · [Explore the project](#explore-the-project)

## Why it exists

During stressful periods, finding forms, invoices and medical information
across apps kept breaking my flow. Even groceries meant searching a WhatsApp
thread while shopping with a child.

I wanted to capture things in the moment and find them when needed—without
maintaining another app or reconstructing the context each time.
[The story and design decisions →](docs/CASE-STUDY.md)

## In conversation

*Illustrative conversations with fictional details.*

### Groceries

| Speaker | Fridge video + voice |
|:--|:--|
| **You** | Coloca esse iogurte, o leite e os ovos no Costco.<br>Se não tiver esse iogurte, pode ser o natural. |
| **Shadow** | Adicionei os três.<br>Anotei iogurte natural como alternativa. |
| **You** | Peguei o leite. |
| **Shadow** | Leite comprado. Faltam o iogurte e os ovos. |
| **You** | Na verdade, ainda não peguei. |
| **Shadow** | Coloquei o leite de volta na lista. |

**In use:** shared lists with three invited people.<br>
[Full conversation](docs/CHAT-EXAMPLES.md#groceries-a-video-becomes-a-shared-list) · [Illustration](assets/brand/conversation-grocery.svg)

### Doctor

| Speaker | Preferences → confirmation |
|:--|:--|
| **You** | Find a dermatologist within 10 miles of 10001.<br>Weekday mornings. My plan is ExamplePlan. |
| **Shadow** | Dermatology, 10 miles, weekday mornings, ExamplePlan.<br>Shall I search? |
| **You** | Yes, search. |
| **Shadow** | I’ll send the options when they’re ready. |

**Early testing:** the person confirms the search and approves outreach; no appointment booking.<br>
[Full conversation](docs/CHAT-EXAMPLES.md#doctor-intake-without-repeating-what-was-already-said) · [Illustration](assets/brand/conversation-doctor.svg)

### Personal records

| Speaker | Question → source |
|:--|:--|
| **You** | How much did I pay for the last HVAC repair? |
| **Shadow** | The latest matching receipt I found is **$480**, dated June 12, 2026. It's marked paid.<br>Source: “Example HVAC — repair receipt.” |
| **You** | Does that include the follow-up reminder? |
| **Shadow** | The reminder is for the same repair, not another payment. The receipt shows $480. |

My personal agent also handled a defective-product support request from a
photo: found the purchase and support contact, extracted the serial number,
sent the details and followed up when support replied.
[Full conversation & tool boundaries](docs/CHAT-EXAMPLES.md#personal-records-find-the-source-behind-the-answer) · [Illustration](assets/brand/conversation-life-index.svg)

**More exchanges:** [Screenshots](docs/CHAT-EXAMPLES.md#groceries-bring-in-a-note-or-another-conversation) · [What’s missing?](docs/CHAT-EXAMPLES.md#groceries-whats-missing-with-purchase-history) · [Private lists](docs/CHAT-EXAMPLES.md#groceries-keep-a-personal-list-separate) · [Recipes](docs/CHAT-EXAMPLES.md#recipes-personal-telegram-and-invited-whatsapp) · [Doctor replies](docs/CHAT-EXAMPLES.md#doctor-a-reply-is-not-a-booking)

## How it's built

OpenClaw provides the gateway, agent runtime and plugin ecosystem. My custom
work covers conversational workflows, shared state, retrieval, media handling
and access checks.

### Access

| Agent | Available tools |
|:--|:--|
| **Personal · Telegram** | Broader configured OpenClaw tools, including Firecrawl research and calendar capture |
| **Invited · WhatsApp** | Grocery and Doctor only; Doctor remains in early testing |

Personal record services remain read-only; the support workflow uses the broader personal agent.

### Stack & workstation

September 2026 snapshot; version labels distinguish host, review and lockfile.

| Technology | Version / source |
|---|---|
| OpenClaw · Node.js | 2026.9.4 · 24.19.0 — documented host |
| Python · SQLite | 3.12.3 · 3.45.1 — local review |
| TypeScript · Vitest | 5.9.3 · 3.2.7 — Grocery lockfile |
| llama.cpp | b10604 — benchmark runner |
| whisper.cpp · GStreamer | Exact builds not recorded |

**Hardware:** EliteMini · Ryzen 7 8745H · Radeon 780M · ~29 GiB usable RAM · 1 TB NVMe.<br>
**Configuration:** Ubuntu 24.04.4 LTS, isolated OpenClaw account, loopback gateway,
systemd user services, SQLite, cloud models and local media processing.
[Versions and configuration →](docs/ARCHITECTURE.md#technology-and-version-snapshot)

### Lessons from use

- **Conversation:** short replies and corrections need as much care as the first request.
- **Authority:** models interpret intent; code checks identity, access and approval.
- **Validation:** real conversations expose failures that isolated tests miss.

[Decisions, experiments and lessons →](docs/CASE-STUDY.md#what-real-use-changed)

## Explore the project

| If you want to… | Start here |
|:--|:--|
| Understand the problem and my contribution | [Case study](docs/CASE-STUDY.md) |
| Inspect the implementation | [Architecture](docs/ARCHITECTURE.md) · [Code & setup](tools/README.md) |
| Evaluate evidence and limitations | [Validation](docs/VALIDATION.md) · [Security](docs/SECURITY.md) |
| Contribute or browse further | [Contributing](CONTRIBUTING.md) · [Documentation](docs/README.md) |

**Review snapshot:** 1,174 passing cases; 12 skips. Live workflows and TypeScript
suites were not rerun. Known issues include calendar ordering, concurrent Doctor
sends and a plaintext workstation disk.

Code and synthetic fixtures are [MIT licensed](LICENSE). Personal data stays outside the export.
