# ShadowOS visual identity

A small identity for a personal project shared as open source and a portfolio.
The graphics should make the work easier to understand and feel approachable.
The project is still developing; presentation should reflect its actual stage.

[Project home](../README.md) · [Documentation guide](README.md)

## Assets

| Asset | Use |
|---|---|
| [Conversation mark](../assets/brand/shadowos-mark.svg) | A simple conversation shape with an offset shape behind it; use with the ShadowOS name |
| [README banner](../assets/brand/readme-banner.svg) | A quiet introduction to groceries, Doctor and personal records |
| [Grocery workflow](../assets/brand/workflow.svg) | Explain how media, instructions and list state fit together |
| [Grocery conversation](../assets/brand/conversation-grocery.svg) | A Portuguese voice update and shared-list change |
| [Doctor conversation](../assets/brand/conversation-doctor.svg) | Search preferences and confirmation, labeled early testing |
| [Personal-record conversation](../assets/brand/conversation-life-index.svg) | Source-backed retrieval and a separately labeled proposed support flow |

All assets are editable SVGs under the repository's MIT license. They contain
no external fonts, images, scripts or tracking. Each has a title and text
alternative. The opaque graphite surface keeps the assets dark on either GitHub page
theme; rounded outer corners remain transparent. GitHub controls the font
and theme of the surrounding Markdown.

## Palette and type

| Role | Color |
|---|---|
| Graphite background | `#181F25` |
| Slate card | `#222A31` |
| Primary text | `#E7ECEF` |
| Secondary text | `#AAB6BF` |
| Mint accent | `#A6CDBE` |
| Muted mint surface | `#253831` |
| Border | `#35414B` |

Use Inter, Segoe UI, Arial, then sans-serif as fallbacks. Assets do not fetch
fonts: the renderer uses the first available family. Use regular 400 for
body text and medium 500 for headings. Keep typography quiet, spacing open,
and shapes flat; avoid oversized bold headlines and high-glow effects.

Conversation cards are portrait layouts with 28px dialogue on a 720px canvas.
Their smaller state labels are secondary to the exchange. The symbol supports
the name; it does not stand for a security guarantee or an operating system.

## Voice

- Lead with a recognizable task and show what happens next.
- Use first person for the motivation and choices behind the project.
- Name OpenClaw's contribution alongside the custom work.
- Keep the current stage visible: grocery in use, Doctor in early testing,
  personal records owner-only, and proposed support composition labeled separately.
- Distinguish illustrated conversations, observed use and measured results.
- Preserve Portuguese and English naturally in examples. Keep navigation
  and explanations in English so new readers can follow the story.

“A little less life admin” is the project's intent, not a measured outcome.
Avoid invented testimonials, adoption counts, availability claims, or calls
to sign up for a service that does not exist.

## Updating graphics

Keep important facts in Markdown as well as in images. Give each image
meaningful alt text. Check the rendered SVG at its intended desktop size
and at 343 pixels wide; the portrait conversation cards and grocery illustration are designed to
remain readable on a small screen. For diagrams with different access
boundaries, show those boundaries explicitly or explain them in nearby text.
