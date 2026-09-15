# Shadow in conversation

[Project home](../README.md) · [Case study](CASE-STUDY.md) · [How it works](HOUSEHOLD-TOOLS.md)

Portuguese and English examples of the interactions Shadow is designed to
support: capture something quickly, carry context across turns, and make the
next action clear. Explanatory notes stay in English; each exchange follows
the member’s language, while the illustrated practice email stays in English.

**These are authored illustrations, not customer transcripts.** Products,
people, provider results, addresses and codes are fictional. The grocery
walkthrough's list operations were checked locally; media understanding and
conversational wording are illustrated. **Doctor is in early testing and has
not fully launched.** See [validation and limits](VALIDATION.md#portfolio-example).

## Three illustrated conversations

Each card opens as an SVG. The first two show household workflows;
personal-record queries belong to the owner's agent. The support-email
portion reconstructs a workflow the owner reports completing with personal
Shadow; it is not a customer transcript.

- [Groceries: a short voice update](../assets/brand/conversation-grocery.svg)
- [Doctor: preferences and confirmation](../assets/brand/conversation-doctor.svg)
- [Personal records: dates, receipts and completed support workflow](../assets/brand/conversation-life-index.svg)

## Groceries: a video becomes a shared list

A member films three products in the fridge: strawberry yogurt, lactose-free
milk and eggs. Costco is the household's current shopping list.

> **Member · short video + narration:** Coloca esse iogurte, o leite e os
> ovos na lista do Costco. Se não tiver esse iogurte, pode ser o natural.
>
> **Shadow:** Adicionei à lista do **Costco**:\
> 🛒 Iogurte de morango — se não tiver, iogurte natural\
> 🛒 Leite sem lactose\
> 🛒 Ovos
>
> **Housemate:** O que falta comprar no Costco?
>
> **Shadow:** Iogurte de morango, leite sem lactose e ovos. Se não tiver
> o de morango, pode ser iogurte natural.
>
> **Housemate · voice note:** Peguei o leite.
>
> **Shadow:** Marquei o leite sem lactose como comprado. Faltam o iogurte e os ovos.
>
> **Housemate · voice note:** Na verdade, ainda não peguei.
>
> **Shadow:** Coloquei o leite sem lactose de volta na lista.

The video identifies products, narration adds a condition, and both people
work from the same list. The alternative is an item note; store availability
and substitution are decisions for the shopper.

## Groceries: bring in a note or another conversation

This continues the list above after the correction.

> **Member · screenshot of a note:** [Arroz / feijão] Coloca esses também.
>
> **Shadow:** Adicionei arroz e feijão à lista do Costco.
>
> **Housemate:** O que ainda falta?
>
> **Shadow:** Iogurte de morango, leite sem lactose, ovos, arroz e feijão.
> A alternativa de iogurte natural continua anotada.

A screenshot of a conversation can be used the same way:

> **Member · screenshot of a family chat:** [“Acabou a banana.”]
> Coloca na lista o que está faltando nessa conversa.
>
> **Shadow:** Adicionei bananas à lista do Costco.

The member supplies the instruction; the screenshot supplies the item.

## Groceries: finish the clear part, clarify the rest

In this separate example, the list contains milk and two kinds of paper towels.

> **Member · voice note:** Got the milk and the paper towels.
>
> **Shadow:** Marked milk as bought. Which paper towels did you get?\
> 1. Bounty\
> 2. Generic\
> I haven’t marked the paper towels yet.
>
> **Member:** The second one.
>
> **Shadow:** Marked generic paper towels as bought. Bounty is still on the list.

The short answer resolves the pending choice. The already completed milk
update is preserved. When the input itself is unclear, the conversation
asks for the missing detail:

> **Member · blurry product photo:** Add this one.
>
> **Shadow:** I can’t read the label clearly. Which product is it?
>
> **Member:** Plain Greek yogurt.
>
> **Shadow:** Added plain Greek yogurt to Costco.

## Groceries: “what’s missing?” with purchase history

Invited users have started asking this kind of question. This illustrative
household has coffee and bananas in a previous completed trip, with eggs on
the current list.

> **Member · voice note:** What’s missing? What did we get last time?
>
> **Shadow:** Eggs are still on your current list. Your last completed trip
> included coffee and bananas; neither is on the current list. Do you need
> either again?
>
> **Member:** Just coffee. We still have bananas.
>
> **Shadow:** Added coffee to Costco.

History helps the person recall purchases. It does not establish what is
currently in the fridge or pantry.

## Recipes: personal Telegram and invited WhatsApp

**Personal Shadow · Telegram.** The owner has Firecrawl available through
OpenClaw, alongside the grocery functionality. Assume this fictional recipe
page lists chickpeas, tomatoes, parsley and lemon.

> **Owner · recipe link:** Dá uma olhada nessa receita e coloca os ingredientes
> na lista do Costco. Limão já temos.
>
> **Shadow:** A receita leva grão-de-bico, tomate, salsinha e limão.
> Adicionei grão-de-bico, tomate e salsinha ao Costco. Deixei o limão de fora.

This combines page retrieval with a grocery action. The grocery engine
itself does not fetch the recipe URL.

**Invited-user Shadow · WhatsApp.** General web scraping is outside this
agent’s current access; the member can provide the ingredients directly.

> **Member · recipe link:** Add the ingredients for this.
>
> **Shadow:** I can work from the ingredients if you paste them here or
> send a screenshot.
>
> **Member · screenshot:** [Chickpeas / tomatoes / parsley / lemon]
> We already have lemon.
>
> **Shadow:** Added chickpeas, tomatoes and parsley to Costco.

## Doctor: intake without repeating what was already said

**Early-testing workflow.** This is an administrative provider search;
all practice names and search results below are invented for the example.

> **Member:** Help me find a dermatologist for myself near 10001.
>
> **Shadow:** What times work for you? You can also give me your plan name
> and preferred search distance.
>
> **Member · voice note:** Weekday mornings. ExamplePlan, within 10 miles.
>
> **Shadow:** Please confirm: a dermatology appointment for you, near
> 10001, within 10 miles, weekday mornings, with ExamplePlan.
>
> **Member:** Yes, search.
>
> **Shadow:** I’ll search and send the options here when they’re ready.

The intake carries forward the patient, location and requested specialty.
Search begins after the member confirms the summary.

## Doctor: compare options and choose who to contact

Continuing the fictional request, a background search has returned options.
Distances and website findings here are illustrative, not real provider data.

> **Shadow:** Two options for your dermatology search:\
> **1. Example Skin Group** — 2 miles away; its website lists dermatology.\
> **2. Sample Dermatology** — 6 miles away; its website lists dermatology.\
> Neither website states whether it accepts ExamplePlan or is taking new
> patients. Those details need confirmation with the practice.
>
> **Member:** Let’s try the first one.
>
> **Shadow:** Selected Example Skin Group. I can prepare an email asking
> about appointments and your plan, or share the practice’s contact details
> so you can reach out yourself.
>
> **Member:** Prepare an email. My name is Sam and my email is sam@example.com.
>
> **Shadow:** I’ll verify your email address first. Enter the code sent there.
>
> **Member:** 482731
>
> **Shadow:** Your email is verified. I’ll prepare the draft for review.

Verification codes are delivered to the member’s email. The number above
is fictional. Selecting a practice does not send an inquiry or book a visit.

## Doctor: review the full email, then approve

Continuing the example, the practice contact was found on its own website.
The full draft arrives as a separate message. All details below are fictional.

> **Shadow · draft:**\
> **To:** frontdesk@skinclinic.example\
> **Cc:** sam@example.com\
> **Subject:** Appointment request, new patient [REQ-A1B2C3]
>
> Hello Example Skin Group,
>
> I'm writing on behalf of Sam, who is looking for a new-patient appointment
> (dermatology) for: me.\
> Insurance: ExamplePlan.\
> Preferred times: weekday mornings.
>
> Could you share your next available appointments and confirm whether you
> accept this plan? You can reply to this email; reply-all is fine.
>
> Sent by a household assistant for Sam. Reference: REQ-A1B2C3\
> Reply STOP to receive no further messages about this request.
>
> If it looks right, send `/ok A1B2C3` to email it.
> To change something, just tell me.
>
> **Member:** Looks good.
>
> **Shadow:** To send this draft, use `/ok A1B2C3`.
>
> **Member:** /ok A1B2C3
>
> **Shadow:** Email sent to frontdesk@skinclinic.example.

Only the member’s direct approval command authorizes sending the reviewed
draft. This illustrated send did not contact a real practice. The workflow
asks about availability; the person completes any appointment booking.

## Doctor: widen a search deliberately

In a separate example, a search finds no matching practices within the
member’s requested 10-mile radius.

> **Shadow:** Não encontrei clínicas que indiquem oferecer o atendimento
> solicitado num raio de 10 milhas. Quer tentar uma área maior?
>
> **Member:** Tenta 25 milhas, mas mantém de manhã em dias úteis.
>
> **Shadow:** Vou buscar num raio de 25 milhas e manter a preferência
> por manhãs em dias úteis. Te envio as opções quando estiverem prontas.

The radius changes at the member’s request. A stated availability preference
still needs confirmation with the practice.

## Personal records: find the source behind the answer

These fictional exchanges use the owner's personal agent. Life-index,
mail-context and the ledger offer complementary read-only queries; invited
household users do not receive access to these records.

### When did I change landscapers?

> **Owner:** When did I change to my current landscaper again?
>
> **Shadow:** March 2025. The welcome email says service started on
> March 10. Source: “Service welcome — Example Lawn.”
>
> **Owner:** Was that the first invoice date, or when they actually started?
>
> **Shadow:** March 10 is the service-start date stated in the welcome
> email. The first invoice is dated March 31.

The answer distinguishes an event date from a document date. If only an
invoice were available, the response should say what that evidence supports.

### What did the last HVAC repair cost?

> **Owner:** How much did I pay for HVAC repair last time?
>
> **Shadow:** The latest matching receipt I found is $480 on June 12, 2026,
> marked paid. Source: “Example HVAC — repair receipt.”
>
> **Owner:** Does that include the follow-up reminder?
>
> **Shadow:** I found the reminder for the same repair. It isn't another
> payment; the receipt still shows $480.

These dates, amounts and source documents are invented for the example.
Grouping related records helps avoid counting an invoice and a reminder
twice. A ledger total still needs the underlying evidence: an invoice,
quote or extracted amount alone is not proof of payment.

### An April purchase, a defect photo and a support email

**Completed with personal Shadow, as reported by the owner.** Shadow found
the purchase details and customer support contact, described the problem
from the supplied photo, extracted details such as the serial number, sent
the inquiry and followed up with the owner when support responded.

The exchange below reconstructs that sequence with a fictional product,
dates, identifiers and wording. It is not a transcript or an independent
replay of the completed workflow.

> **Owner · photo of cracked fan housing:** Email technical support about
> the defective fan I bought in April. Here's a picture of the defect.
>
> **Shadow:** I found the April 18 order for the Example Air desk fan and
> the customer support contact. I extracted the serial number and described
> the cracked housing from your photo. I sent those details to support.

The reconstructed inquiry illustrates the information the agent assembled:

> **To:** support@example.com\
> **Subject:** Defective desk fan — order EXAMPLE-0418
>
> Hello,
>
> I purchased an Example Air desk fan on April 18, 2026. Its housing has
> cracked. Could you help with repair or replacement options?
>
> Order: EXAMPLE-0418\
> Serial number: EXAMPLE-SN-0042
>
> Thank you,\
> Sam
>
> **Shadow · later:** Support has replied. Here's their response and the
> next step they're asking for.

The fan, defect description, recipient, order and serial number above are
invented to illustrate the interaction. The actual workflow was reported by
the owner; no new email was sent and no real photo or receipt was processed
for this documentation update. This account does not establish the exact
email connector, attachment handling, approval mechanism or reply-monitoring
implementation used by the personal agent. It shows the user-facing
sequence without inventing those implementation details.

### What the read-only commands contribute

These are illustrative calls to the exported tools, not executed lookups of
personal data. The surrounding agent resolves the request and chooses which
records to inspect.

| Task | Tool call | What it provides |
|---|---|---|
| Find the landscaper correspondence | `artifact_search({"query":"Example Lawn welcome"})` | Matching catalog entries, metadata and permitted excerpts |
| Find the HVAC repair evidence | `artifact_search({"query":"Example HVAC repair"})` | Candidate records to inspect; the query does not itself prove payment |
| Inspect a matching document | `artifact_get({"sha256":"<digest returned by search>"})` | Its metadata, extracted fields, file path and any text allowed by policy |
| Retrieve amounts across documents | `artifact_fields({"key":"amount_usd","limit":25})` | Extracted values with document identifiers; each must be matched to the relevant record |
| Inspect the known repair counterparty | `ledger_entity({"name":"Example HVAC","limit":10})` | Grouped events and evidence from the indexed ledger |
| Find April purchase candidates | `ledger_search({"kind":"purchase","since":"2026-04-01","until":"2026-04-30"})` | Candidate purchases; confirm the date and product against the returned records |
| Find the receipt in the catalog | `artifact_search({"query":"Example Air receipt"})` | Matching documents that could supply purchase context for support |

The [life-index server](../layers/openclaw/life-index/lifeindex/mcp.py) opens
its store read-only. By default, tier 1 and 2 documents withhold their text;
metadata and permitted fields may not be enough to answer every question.
The [ledger server](../layers/openclaw/ledger/mcp.py) and
[mail-context server](../layers/openclaw/mail-context/mailctx/mcp.py) are also
read-only. Their tools do not send emails, attach photos, or authorize actions.
The completed support workflow used the owner's broader personal OpenClaw
environment, which includes capabilities beyond these retrieval services.
Its full implementation is not exported here. The Doctor `/ok` command
belongs to Doctor outreach; this account does not imply it governed the
personal support inquiry.

## Design and implementation references

- [Multimodal behavior and tool boundaries](HOUSEHOLD-TOOLS.md#multimodal-input-and-conversational-behavior)
- [Grocery interaction instructions](../tools/grocery-list/skill/SKILL.md)
- [Doctor conversation instructions](../tools/household-config/prompts/DOCTOR.md)
- [Doctor outreach template and approval checks](../tools/doctor-search/core/outreach.py)
- [Evidence and validation limits](VALIDATION.md)
