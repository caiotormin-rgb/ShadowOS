# ShadowOS visual identity

A small identity for a personal project shared as open source and a portfolio.
Lead with what the tools do. Give the conversations room to explain the work.

[Project home](../README.md) · [Documentation guide](README.md)

## Header direction

The header contains the conversation mark, ShadowOS name and one sentence:
“Chat tools for groceries, doctor search, and personal records.” The dark
surface, restrained type and open spacing keep the introduction simple.
Current stage, access boundaries and OpenClaw attribution stay in readable
Markdown immediately below it.

References inspected for this layout:

- [Supabase](https://github.com/supabase/supabase): a clear mark and wordmark
  paired with a short description.
- [Hoppscotch](https://github.com/hoppscotch/hoppscotch): identity and purpose
  first, with the interface shown separately.
- [Formbricks](https://github.com/formbricks/formbricks): a compact introduction
  followed by visual evidence of what the project does.

These references informed the hierarchy. Their artwork is not included here.

## Assets

| Asset | Use |
|---|---|
| [Conversation mark](../assets/brand/shadowos-mark.svg) | A conversation shape with an offset shape behind it; use with the ShadowOS name |
| [README banner](../assets/brand/readme-banner.svg) | Wordmark and a concrete description of the tools |
| [Grocery workflow](../assets/brand/workflow.svg) | Explain how media, instructions and list state fit together |
| [Grocery conversation](../assets/brand/conversation-grocery.svg) | A Portuguese voice update and shared-list change |
| [Doctor conversation](../assets/brand/conversation-doctor.svg) | Search preferences and confirmation, labeled early testing |
| [Personal-record conversation](../assets/brand/conversation-life-index.svg) | Source-backed retrieval and a reconstructed, owner-reported support workflow |

The exported SVGs contain vector outlines, with no font downloads, external
images, scripts or tracking. Each retains a title and description. Editable
text lives in [source](../assets/brand/source). The artwork and build script
use the repository's MIT license; bundled Geist fonts use the
[SIL Open Font License 1.1](../assets/brand/fonts/OFL.txt).

## Palette and type

| Role | Color |
|---|---|
| Header background | `#171D23` |
| Header wordmark / description | `#EDF0F2` / `#BBC5CC` |
| Conversation background | `#181F25` |
| Slate card | `#222A31` |
| Primary / secondary text | `#E7ECEF` / `#AAB6BF` |
| Mint accent | `#A6CDBE` |
| User bubble / mark shadow | `#253331` / `#253831` |
| Border | `#35414B` |

Use **Geist Regular (400)** for dialogue and descriptions and **Geist Medium
(500)** for headings. The build shapes the actual font and converts letters
to paths so viewers see the intended typography regardless of installed fonts.
GitHub controls the font and theme of the surrounding Markdown.

The header is 1200 × 320, with a 72px wordmark and 38px description.
Conversation cards use 28px dialogue on a 720px canvas. Keep shapes flat and
spacing generous. The opaque background stays dark on either GitHub theme;
rounded outer corners remain transparent. The symbol represents conversation,
not a security guarantee or an operating system.

## Updating graphics

1. Edit the corresponding SVG in [source](../assets/brand/source). Text nodes
   use explicit positions and line breaks; there is no automatic text wrapping.
2. Build from the repository root with Python 3.9+ and the HarfBuzz shared
   library available on the system:

   ```bash
   python3 -m venv /tmp/shadowos-brand-venv
   /tmp/shadowos-brand-venv/bin/python -m pip install -r assets/brand/requirements.txt
   /tmp/shadowos-brand-venv/bin/python assets/brand/build.py
   ```

   The build reads the bundled fonts and writes the six SVG exports in
   `assets/brand/`. Its JSON output lists text positions and measured widths.
   These are optional graphics dependencies, separate from the runtime tools.
3. Inspect exports at their native width and 343px wide. Check line lengths,
   bubble padding and accented characters. Commit sources and rebuilt exports.

Keep important facts in Markdown as well as images, and give each image
meaningful alt text. Outlined lettering is not selectable text. Text versions
of the examples remain in the README and [chat examples](CHAT-EXAMPLES.md).

### Font provenance

The unchanged Geist Regular and Medium TTF files come from
[vercel/geist-font at commit 10dc765](https://github.com/vercel/geist-font/tree/10dc7658f13c38a474cde201bb09a4617267545b/fonts/Geist/ttf).
The included license comes from the same revision; only trailing whitespace
was removed. Copyright 2024 The Geist Project Authors.

| File | SHA-256 |
|---|---|
| `Geist-Regular.ttf` | `85a1c6b18a6b0a06dfe9fd4f6d6a5d4979f74ec861eaef4bc7868b5492b8a117` |
| `Geist-Medium.ttf` | `3a3b36f0d0b981f4857f7f00eeef4a5ee123605575d362ab31ee7c19e3d11f2f` |

## Voice

- Lead with a recognizable task and show what happens next.
- Use first person for the motivation and choices behind the project.
- Name OpenClaw's contribution alongside the custom work.
- Keep the current stage visible: grocery in use, Doctor in early testing,
  personal records owner-only, and completed personal workflows distinguished
  from exported implementation and independent validation.
- Distinguish illustrated conversations, observed use and measured results.
- Preserve Portuguese and English naturally in examples. Keep navigation
  and explanations in English so new readers can follow the story.

Avoid invented testimonials, adoption counts, availability claims, or calls
to sign up for a service that does not exist.
