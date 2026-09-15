# Building a personal assistant around everyday needs

*Caio Tormin · August–September 2026 · one workstation · built with coding agents*

ShadowOS is an evolving personal assistant inside OpenClaw. I use it to
organize information, work through ideas and build solutions around my own
needs, with WhatsApp and Telegram as familiar ways in. The first use case
was retrieving a payment total; the project expanded into shared grocery
workflows used by three invited people. Doctor finder is in early testing.
These are current applications of the assistant, which I continue to extend.

[Project home](../README.md) · [Conversations](CHAT-EXAMPLES.md) · [Architecture](ARCHITECTURE.md)

## At a glance

| | |
|---|---|
| **Problem** | Everyday information retrieval and household coordination repeatedly interrupted primary work |
| **Approach** | Familiar chat apps, easy multimodal input, shared state and explicit review for selected actions |
| **My role** | The problem framing and input requirements came from my experience; I built the project with coding agents |
| **Custom scope** | Grocery and Doctor workflows, information retrieval, media processing and access checks inside OpenClaw |
| **Current stage** | Personal use and three invited grocery users; Doctor in early testing |
| **Evidence** | Observed household use and scoped technical validation; time saved and end-to-end task success remain unmeasured |

## The personal motivation

I went through periods of high stress and workload when my attention became
a critical bottleneck. Payee details, documents, old forms, invoices and
medical information were scattered across my iPhone, email, Mac, provider
websites and TurboTax. Finding what I needed pulled me away from my primary
tasks.

Email was often the place I fell back on to retrieve that information.
Searching messages and reconstructing the context would break my flow, so
I often procrastinated on the task instead. I wanted to reduce the attention
it took to find the information and get started.

That motivation shaped the project: make data and ideas easy to capture,
organize them, and bring them back in the context where they are needed.
I wanted less tool switching and less work reconstructing the background
before I could act. A personal assistant I can customize lets me keep
building solutions as different needs appear, using the chat apps I already
use. The devices and services above describe where the information was
scattered; they are not a list of integrations implemented by ShadowOS.

## The first concrete question

Email had become my archive for contracts, school forms and invoices. I
could find a particular message, but answering a question across many
messages took work. Losing track of irregular contractor invoices made the
problem concrete: **what did I pay the contractor?**

A useful answer needed to group the right counterparty, distinguish repeated
reminders from separate events, and make the underlying records inspectable.
Searching for a keyword alone did not settle the total. Sending the whole
mailbox to a model would add cost and expose much more data than the question
required.

## Building for friends and family

I also wanted to make tools that help friends and family organize information.
Groceries made the coordination problem tangible. Our list lived in an
ongoing WhatsApp thread. While shopping with a child, I had to keep taking
out my phone, reading back through messages and working out what had been
purchased. People in the house had different understandings of what was
needed, leading to missed purchases or buying too much.

I had tried notes and purpose-specific apps. They still required another app
to open and maintain. Easy input was critical: conversational tools offered
a familiar way to contribute while a shared list tracked what was needed,
bought and still outstanding. This turned the choice of interface into a
product requirement.

Doctor search involved a similar sequence of interruptions: finding providers
around my region, curating the options and contacting practices individually.
The application is intended to automate that legwork with light human
oversight. The person supplies the need, chooses providers and approves the
outreach draft. The current workflow does not book appointments.

## Making multimodal conversation useful

I worked extensively on multimodal input and conversational intelligence.
One important interaction is a short video showing several products in the
fridge while the person speaks: add these products, and if one is unavailable,
choose a stated alternative. The task combines visual identification with
intent, references such as “this one,” and conditions spoken during capture.

Other inputs include photos of conversations, screenshots of notes from
other apps, and recipe links. The aim is to let people bring information
from wherever it already exists. My personal Telegram agent can use
Firecrawl to read a page and then use the same grocery functionality.
Invited users currently have only grocery and doctor tools: they can provide
recipe ingredients as text or a screenshot. The grocery engine itself does
not fetch pages.

The conversation must also survive the next turn: a short voice reply, a
correction to a purchase, or a reference to the item just shown. The intended
behavior is to preserve the language, store and task, ask about unclear
recognition, and carry out only the unresolved part of a mixed request.
Conditional substitutions can be recorded as notes on the requested item.

This work reached below the prompt. Local transcription handles language
selection, short answers and retry limits. The transcript cleaner explicitly
preserves spoken answers such as “sim” and “não,” because removing them as
filler would break a clarification loop. Short video handling combines audio
and sampled frames, with explicit limits and failure reporting.

Invited users now use voice notes extensively and have started consulting
purchase history for “what's missing?” These are reported usage observations;
recognition accuracy and end-to-end task success have not been measured in
this portfolio. The [grocery walkthrough](CHAT-EXAMPLES.md#groceries-a-video-becomes-a-shared-list)
illustrates the behavior with synthetic inputs. [Portuguese and English chat examples](CHAT-EXAMPLES.md)
show further grocery interactions and the Doctor workflow in early testing.

## Project scope and contribution

The problems and input requirements above came from my own experience and
my goal of making useful tools for friends and family. The project was built
with coding agents and lives inside the OpenClaw environment I use daily,
with a broad collection of existing plugins and tools. OpenClaw supplies the
messaging gateway, agent runtime and plugin ecosystem. The custom work here
covers the mail read model, ledger and document query services; grocery and
doctor workflows; access policies; media processing; and deployment checks.

## The broader environment in daily use

My personal Shadow on Telegram has broader configured OpenClaw capabilities,
including Firecrawl for web scraping. I use that environment for shopping
comparisons, including car leases, business-idea research and calendar
capture. It can combine retrieved web content with the same grocery
functionality available to invited users. Calendar tools turn information from email, WhatsApp
conversations and medical appointments into calendar events.

One completed personal workflow brought several capabilities together:
I sent a photo of a defective purchase, and Shadow found the purchase details,
identified customer support, described the problem from the photo, extracted
details such as the serial number, and sent the inquiry. It followed up with
me when a response arrived. This is my reported experience with the broader
personal agent; the full sending and reply-handling integration is outside
this export. [Illustrated support conversation](CHAT-EXAMPLES.md#an-april-purchase-a-defect-photo-and-a-support-email).

This public tree includes selected custom work from that daily setup. Its
calendar guardrail processes email `.ics` attachments; the car-lease research
and other calendar-capture implementations are outside the export.

The implementation spans two private repos. This export makes the code,
synthetic fixtures and selected development history inspectable without
publishing the household's data or the private implementation records.
The environment runs on my Ubuntu workstation: an EliteMini-series machine
with a Ryzen 7 8745H and a 1 TB NVMe drive. It combines cloud models with local
inference and transcription. [Workstation and configuration](ARCHITECTURE.md#workstation-and-configuration).

## Decision 1: organize the data around the question

The ledger's unit is a counterparty and an event. One merchant may send from
many addresses; an invoice may be followed by several reminders. Resolving
sender identities and deduplicating those records gives the query a useful
meaning before a model explains the answer.

The deployment's raw extraction produced 2,374 rows and 1,813 events after
deduplication. In the original contractor example, summing the raw records
would have produced nearly three times the deduplicated total. That example
showed why the aggregation mattered to the user.

**Limit:** an extracted invoice is not proof of payment. Precision was 84%
in a hand-check of 50 random rows, and only 28% of ledger rows contained an
amount in the available metadata/snippets. The ledger helps locate and
reconcile evidence; it does not establish a complete payment history.

## Decision 2: spend model effort where it changes the result

The mail pipeline filters by sender and structure, then reuses extraction
rules for messages sharing a format. In the corpus analysis, 154 senders
covered 80% of non-promotional messages. That suggested a budget of about
154 initial template-learning calls, assuming one successful call per sender.
It is a planning estimate, not a measured count of successful model calls.
Format changes, failures and retries can require more work.

A local model was evaluated for sender classification and reached 0.864
agreement, but a deterministic pass handled the classification, so that
model step was dropped. A custom Drive index was also retired once the
existing connector supplied search, full text and OCR.

The resulting design uses models for interpretation and curation, while code
handles ownership, state changes and confirmation checks. The
[decision register](plans/00-mail-and-documents-MASTER.md) preserves the
rejected approaches and the reasons behind them.

## Decision 3: make household use a separate access surface

My personal Telegram agent can query personal context, use Firecrawl and
other configured OpenClaw tools, and work with groceries. Invited household
members use a separate WhatsApp agent limited to grocery and doctor-finder
tools. I have not opened the full OpenClaw capabilities to them for security;
Doctor is still in early testing.

Their identities and grants come from the host and access store; the model
does not choose who it acts as. Sharing grocery functionality does not give
an invited user the owner's web-scraping or other general tools.

That access model supports different kinds of interaction:

- Add groceries and mark purchases through ordinary conversation.
- Preview a removal and require the member to type `/remover CODE`.
- Research doctors and prepare an email draft, then require `/ok CODE`
  before sending it from a dedicated mailbox.

A domain router limits household tools to the selected Grocery or Doctor
mode. The scheduled calendar guardrail is a separate automatic writer; the
read-only guarantee applies to the context query services, not to every
component of ShadowOS. [Security scope](SECURITY.md).

## What real use changed

The grocery tools reached three invited users in early September. Voice
notes are used extensively, and users have begun consulting purchase history
for missing items. Doctor finder remains in early testing and has not fully
launched. There is no published measurement of time saved, retention or task
completion.

The first conversations exposed failures that unit tests had missed:

- **Portuguese voice notes produced English filler or no text.** Local
  transcription gained per-chunk language detection, a retry path and a
  time budget, then was checked against household samples. The recordings
  remain private.
- **A mode switch succeeded, but the next request asked the member to switch
  again.** The denial message confused an unavailable tool with a missing
  mode. A second problem involved the runtime's tool broker. Reproducing
  the real dispatch path led to policy fixes and regression cases.
- **A deployment remained two releases behind.** A manual update step was
  replaced with copyable units and an automated path check. The operational
  lesson became an executable check rather than another reminder.

These incidents connect product behavior to engineering decisions. Further
workflow detail and the selected commit trail are in
[Household tools](HOUSEHOLD-TOOLS.md) and [History](HISTORY.md).

## Evidence, with its scope

The following figures were reported from the private deployment. The raw
corpus and personal implementation records are not published here.

| Evidence | Result | What it establishes |
|---|---|---|
| Mail corpus | 132,588 messages, 2008–2026 | Scale of the indexed source, not retrieval accuracy |
| Ledger | 1,813 deduplicated events, 1,102 counterparties | Extracted coverage, not a complete financial record |
| Extraction sample | 84% precision on 50 random rows | A small quality check; recall was not established |
| MCP query round trip | 32 ms p50, process spawn to tool answer | Local tool latency; excludes model generation and messaging |
| Ledger query | 0.13 ms in the reported measurement | Database query latency on the deployment machine |
| Sender coverage estimate | About 154 initial learning calls for 80% coverage | Assumes one successful template-learning call per sender |

The [export validation](VALIDATION.md) records the suites run, skipped cases,
reproduction commands and known correctness issues. It distinguishes a
passing test suite from verification of a live user workflow.

## Current limits and next work

The system runs on a single workstation whose disk remains plaintext under
an explicit operator waiver. The public repo is a filtered export, and its
operational references include historical state. The doctor plugin's package
manifest also needs explicit test/build dependencies before its advertised
fresh-install workflow is dependable.

The next engineering work is to fix the reproduced calendar and email
failures, improve extraction coverage beyond snippets, and complete the
planned encryption migration. A household view of the ledger is planned,
subject to access and consent decisions. Product evaluation still needs a
repeatable measure of whether people complete their intended tasks.

## Related household analysis

A related project, `solar_calc`, applies the same interest in organizing
scattered household information to energy use. It combines a year of solar
production, home consumption and utility cost exports with weather data to
estimate solar savings and explore consumption patterns.

The project includes a report generator, an analysis notebook and charts.
It distinguishes measured energy inputs from EV charging inferred from
nighttime usage, and states that its savings estimate omits export credits
because the source data does not include that compensation. That distinction
between records, estimates and missing inputs is useful beyond this study.

The study credits me as author and OpenAI Codex for coding and analysis
support. This related project is separate from the OpenClaw integrations
documented here. Its household data and financial results remain private.
The portfolio review inspected its code and existing report; the analysis
was not rerun or independently validated.

## Future product direction

I want to extend the same conversational approach to calendars, appointments,
children's activities and travel planning. The common goal is to organize
information and handle the repetitive work, with a person involved at the
points that need judgment or approval.

This direction builds on the research and calendar capture I already use.
The exported calendar guardrail handles email invitations; the separate
Calendar read-model prototype remains unconnected. Broader scheduling,
activity coordination and travel workflows still need design and evaluation.
