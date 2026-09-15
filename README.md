# ShadowOS

**Everyday tasks and personal information, organized through the chat apps
we already use.**

ShadowOS lives within [OpenClaw](https://github.com/openclaw/openclaw), where
I use a broad collection of plugins and tools daily. This repository presents
the custom work I have added to that environment: shared grocery lists,
a doctor-search workflow in early testing, and questions about years of
email and documents through WhatsApp and Telegram. Three invited people use
the grocery tools; personal mail and document queries stay on the owner's
account.

Built by **Caio Tormin with coding agents**, August–September 2026. This is a
public, de-identified export of a system running on one workstation.

## A conversation across formats

The grocery exchange below is in Portuguese; the Doctor example is in
English. Both languages are part of the household experience.

*Synthetic reconstructions of the interactions I designed for. List changes
and reply facts were checked against the grocery engine; media recognition
and conversational interpretation are illustrated.
[Verification details](docs/VALIDATION.md#portfolio-example).*

**A short fridge video, with spoken context.** The member shows strawberry
yogurt, lactose-free milk and eggs, naming a fallback while filming.

> **Member · 8-second video + speech:** Coloca esse iogurte, o leite e os
> ovos na lista do Costco. Se não tiver esse iogurte, pode ser o natural.
>
> **Shadow:** Adicionei à lista do **Costco**:\
> 🛒 Iogurte de morango — se não tiver, iogurte natural\
> 🛒 Leite sem lactose\
> 🛒 Ovos

The image supplies the products; speech supplies the intent and condition.
The fallback is saved as a note on the requested yogurt.

**A voice update and a short correction.** The same conversation keeps the
store and the product in context.

> **Member · voice note:** Peguei o leite.
>
> **Shadow:** Marquei como comprado no **Costco**:\
> ✅ Leite sem lactose\
> Faltam 2 itens.
>
> **Member · voice note:** Na verdade, ainda não peguei.
>
> **Shadow:** De volta à lista do **Costco**:\
> ↩️ Leite sem lactose

**A screenshot from another app.** The member sends a note containing
“arroz” (rice) and “feijão” (beans), without retyping the items.

> **Member · screenshot + caption:** Coloca esses também.
>
> **Shadow:** Adicionei à lista do **Costco**:\
> 🛒 Arroz\
> 🛒 Feijão

A photo of a conversation can serve the same capture role: the member asks
Shadow to add the relevant items. The list stays shared across the household
as people move between voice, camera and text.

I worked extensively on this conversational behavior: interpreting “esse” (“this one”)
from the media and current task, preserving alternatives, accepting short
corrections, and asking when recognition is uncertain. Invited users use
voice notes extensively and have begun consulting purchase history to ask
“what's missing?” [How the interaction is designed](docs/HOUSEHOLD-TOOLS.md#multimodal-input-and-conversational-behavior).

## A doctor search, with the person in control

*Synthetic English example of the Doctor workflow, which is in early testing.*

> **Member:** Find a dermatologist for me near 10001, within 10 miles.
> Weekday mornings work. My plan is ExamplePlan.
>
> **Shadow:** A dermatology appointment for you, within 10 miles of 10001,
> weekday mornings, with ExamplePlan. Shall I search?
>
> **Member:** Yes.
>
> **Shadow:** I’ll search and send the options here when they’re ready.

Once options arrive, the person chooses a practice. Shadow can prepare an
email to ask about availability and insurance; the person reviews the full
draft and sends a direct `/ok CODE` to authorize it. Appointment booking
stays with the person.

[More conversations in Portuguese and English](docs/CHAT-EXAMPLES.md) cover shared shopping,
ambiguous voice updates, purchase history, recipe links, and the Doctor
workflow from intake through approved outreach.

## Why I built it

During periods of high stress and workload, my attention became a critical
bottleneck. Payee details, documents, old forms, invoices and medical
information were scattered across my iPhone, Mac, email, provider websites
and TurboTax. Searching email would break my flow, so I postponed tasks that
depended on that information. I wanted to protect attention for my primary
work.

I also wanted to build tools that organize information for friends and
family. Our grocery list lived in an ongoing WhatsApp thread. Shopping with
a child meant repeatedly pulling out my phone and figuring out what was
still needed. Gaps between household members led to missed purchases or
buying too much. I had tried notes and dedicated apps; entering and updating
information in another app was itself a burden.

Conversational tools offered a way to keep input easy and maintain a shared
list behind the messages. Doctor search had a similar burden: finding local
providers, comparing them and contacting each practice. The aim is to
automate that legwork while leaving the person to choose and approve outreach.

## Two access levels

| Agent | Available capabilities |
|---|---|
| **Personal Shadow · Telegram** | Broader configured OpenClaw tools, including Firecrawl web scraping, plus the same grocery functionality |
| **Invited-user Shadow · WhatsApp** | Strictly grocery and doctor-finder tools; Doctor remains in early testing |

I have deliberately kept the wider OpenClaw capabilities closed to invited
users for security. The grocery experience is shared; access to general web
scraping and other owner tools is separate.

## How I use the broader OpenClaw setup

Through my personal Telegram agent, I use OpenClaw's plugins and tools for
daily research and coordination:

- **Shopping research:** Firecrawl web scraping and research to compare
  options, including car leases. The personal agent can use retrieved page
  content and then the same grocery functionality—for example, working from
  a recipe page to an ingredient list.
- **Business ideas:** web research to explore potential ideas.
- **Calendar capture:** turning information from emails, WhatsApp
  conversations and medical appointments into calendar events.

This export contains selected custom work, including the email `.ics`
calendar guardrail. The [case study](docs/CASE-STUDY.md) explains its scope
and a related household energy analysis.

## What the project adds

OpenClaw provides the gateway, messaging integrations and agent runtime.
The custom work here adds:

| Need | Custom work | Who uses it |
|---|---|---|
| Keep a shopping list current | Shared and private lists, bilingual item matching, purchase tracking, trip history and confirmations | Owner + three invited users |
| Find and contact a doctor | Search and curation, sourced shortlists, verified requester email and approval of the outreach draft | Early testing; not fully launched |
| Find a transaction or document | Searchable mail metadata, a ledger grouped by counterparty, and a document catalog | Owner |
| Keep access appropriate | Person-specific grants, tool restrictions by mode, and checks on native approval commands | Household |
| Handle recurring intake | Local media transcription, a morning brief, and scheduled calendar invitation processing | Owner and household, by tool |

Implementation details: [household tools](docs/HOUSEHOLD-TOOLS.md) ·
[architecture](docs/ARCHITECTURE.md).

## Three decisions that shaped it

- **Make input fit the moment.** Text, voice notes and photos let people
  contribute through chat. Easy capture was a requirement after notes and
  dedicated apps added friction.
- **Keep shared state behind the conversation.** A grocery list tracks what
  is needed and bought across household members. The ledger similarly groups
  related records and deduplicates reminders before presenting a total.
- **Use models where interpretation is needed.** Search planning and language
  understanding use models; membership, stored state and confirmations are
  handled by code. Sender classification was made deterministic after a
  local-model trial.

The [case study](docs/CASE-STUDY.md) explains the choices, results and limits.

## Where it runs

My `torm` workstation is an EliteMini-series machine with an **AMD Ryzen 7
8745H (8 cores / 16 threads), Radeon 780M graphics, about 29 GiB of OS-visible
memory, and a 1 TB Kingston NVMe SSD**, running Ubuntu 24.04.4 LTS. Hardware
was checked on September 15, 2026.

The documented deployment runs OpenClaw under an isolated Linux account,
with a loopback-only gateway and systemd user services. Cloud models handle
agent conversations; local `llama.cpp` supports bounded text work and
`whisper.cpp` transcribes media. SQLite holds workflow and context data.
[Hardware and configuration details](docs/ARCHITECTURE.md#workstation-and-configuration).

## Evidence and limits

- **Use:** three invited grocery users; voice notes are used extensively,
  and users have started consulting purchase history for missing items.
  Doctor finder is in early testing. Time saved and task success have not
  been formally measured.
- **Data:** 132,588 indexed messages from 2008–2026 and 1,813 deduplicated
  ledger events in the private deployment. The corpus is excluded.
- **Quality:** extraction precision was 84% on 50 randomly sampled rows.
  Only 28% of ledger rows had an amount in the available metadata/snippets;
  retrieval should not be treated as a complete account of payments.
- **Verification:** the September 15 export review ran 1,186 Python and Node
  test cases: 1,174 passed and 12 skipped. TypeScript plugin tests were not
  rerun in that review. See [validation scope](docs/VALIDATION.md).

Permissions differ by workflow: context servers expose read-only queries;
grocery removal and doctor outreach require typed approval; the scheduled
calendar guardrail can create or update events automatically. This is an
early system on a single machine with a plaintext disk and known limitations,
not a general deployment package. [Security and boundaries](docs/SECURITY.md).

## Where I want to take it

I want to build on the research and calendar capture I use today with
broader conversational planning for calendars, appointments, children's
activities and travel. The goal is to organize the information, do the
legwork, and keep human decisions lightweight. Those broader planning
workflows are future work; the exported calendar guardrail handles the
narrow task of importing email invitations.

## Explore the work

- [Case study: problem, decisions and results](docs/CASE-STUDY.md)
- [Architecture and data flow](docs/ARCHITECTURE.md)
- [Household workflows and implementation](docs/HOUSEHOLD-TOOLS.md)
- [Test commands and plugin requirements](tools/README.md)
- [Development history](docs/HISTORY.md)

```text
tools/             household workflows, plugins, media and automations
layers/openclaw/   mail, ledger and document context services
station/           operator scripts and deployment checks
docs/              case study, architecture, security and operational references
```

Operational references retain dated deployment details and need review
before reuse. Credentials, personal records, memory, household identities
and recordings are excluded; fixtures use synthetic data.
[Publication boundary](docs/PUBLISHING.md).

MIT licensed.
