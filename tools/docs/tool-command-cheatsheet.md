# Shadow tool command cheat sheet

Use these as natural-language commands. The labels describe where each capability comes from:

- **Official / built-in** — supplied by OpenClaw or the connected platform.
- **Open source** — a third-party open-source tool or skill installed locally.
- **Custom** — a workflow created specifically in this workspace.
- **Hybrid** — a custom workflow built on an official or open-source tool.

## Everyday utilities

### Morning overview — Custom / hybrid

The weekday morning digest is our custom read-only workflow built on Google Workspace access.

- “Give me today’s briefing.”
- “What needs my attention today?”
- “What’s coming up in the next 48 hours?”
- “Summarize my unread important email.”

### Gmail, Calendar, Drive, Docs, Sheets, Contacts — Open source + official APIs

The locally installed open-source `gog` CLI connects to official Google Workspace APIs. Our morning digest and its safety constraints are custom.

- “Find emails from [person] about [topic].”
- “Summarize this email thread.”
- “Show messages that need a reply.”
- “Draft a reply saying [intent].”
- “What’s on my calendar today?”
- “Find a free 30-minute slot this week.”
- “Who is attending my next meeting?”
- “Draft an event for [date/time/purpose].”
- “Find the document about [topic].”
- “Summarize this document.”
- “Find duplicate or outdated files—don’t change anything.”
- “Inspect this spreadsheet and explain the main patterns.”

Sending email; creating, changing, or cancelling events; and moving, editing, sharing, or deleting cloud files require a preview and explicit approval.

### Apple Notes — Official app + open-source/local integration

- “Search my notes for [topic].”
- “Show my recently edited notes.”
- “Create a note titled [title] with this content.”
- “Append this to my [note name] note.”

Deletion or substantial edits should be previewed first.

### Notion — Official service + local integration

- “Find my Notion page about [topic].”
- “Summarize this page.”
- “Draft a new page for [purpose].”
- “Show database items matching [condition].”

### Grocery list — Custom

This workflow was created in this workspace for multimodal lists, trip rollover, and allowlisted sharing.

- “Add milk, eggs, and coffee to my grocery list.”
- “What’s currently on the list?”
- “Mark [item] as bought.”
- “Start a new shopping trip and roll over unfinished items.”
- “Share the list with [allowlisted person].”

### Reminders and schedules — Official / built-in

- “Remind me tomorrow at 9 AM to [task].”
- “Remind me in 45 minutes to [task].”
- “Every Friday at 4 PM, remind me to [task].”
- “List my scheduled reminders.”
- “Cancel the reminder about [topic].”

### Web research — Official / built-in

- “Look this up and give me the answer with sources.”
- “Compare [A] and [B] using current information.”
- “Research this, but keep it to a one-page decision memo.”
- “Check whether an existing free tool already solves this.”

### Opportunity Radar — Custom

- “Evaluate this link with Opportunity Radar.”
- “Score this idea for fit, effort, evidence, and timing.”
- “Give me one bounded next action—not a project plan.”

### Spike — Open-source/OpenClaw skill

- “Run a small throwaway spike to see whether [idea] works.”
- “Compare these two technical approaches with a minimal prototype.”
- “Do not productionize it; just return evidence and a verdict.”

### Diagrams — Open-source/OpenClaw skill

- “Make a simple flowchart of this process.”
- “Diagram the architecture described here.”
- “Turn these steps into an editable whiteboard.”

### Weather — Open-source/OpenClaw skill + public web data

- “What’s the weather today in [location]?”
- “Will it rain during [time window]?”
- “Give me the travel forecast for [dates/location].”

### Creative utilities — Official / built-in and OpenClaw skills

- “Make a meme about [topic].”
- “Generate an image of [description].”
- “Edit this image: [requested change].”

## System health and maintenance

### OpenClaw healthcheck — Official / built-in skill

- “Give me a quick OpenClaw and workspace health check.”
- “Check CPU, memory, disk, gateway, and failed services.”
- “Is anything unhealthy or urgent?”
- “Run a read-only system health audit.”
- “Audit OpenClaw security and configuration; change nothing.”
- “Check gateway responsiveness and exposure.”
- “Inspect workspace hygiene and Git status.”
- “Check memory indexing and test search end to end.”
- “Inspect session bookkeeping without deleting anything.”
- “Show large temporary files and safe cleanup candidates.”

### Device and code diagnostics — Official/OpenClaw skills + open-source debuggers

- “Diagnose why [service/tool] is failing; don’t fix it yet.”
- “Check whether my Mac node is connected.”
- “Debug this Python error.”
- “Debug this Node.js process.”

Python debugging uses standard open-source tools such as `pdb`/`debugpy`; Node.js debugging uses the standard Node inspector and related protocols.

### Skill discovery and creation

- **ClawHub — official OpenClaw ecosystem:** “Search ClawHub for an existing skill that does [task].”
- **Skill Workshop — built-in workflow:** “Propose a reusable skill for [workflow].”
- **Skill Creator — official/OpenClaw skill:** “Audit or improve this existing skill.”

New or changed reusable skills remain proposals until explicitly approved.

## Safe scope controls

Add any of these to a request:

- “Read-only.”
- “Preview changes first.”
- “Do not send, delete, install, or publish anything.”
- “Stop after diagnosis.”
- “Give me one recommendation and one next action.”
- “Keep this bounded to 15 minutes.”

Best default command:

> Run a read-only check, explain what you found, and preview any proposed changes before acting.
