# Presentation decisions

A personal project, an open-source code export and a portfolio of work.

[Project home](../README.md) · [Documentation guide](README.md)

## Settled choices · September 15, 2026

| Decision | Reason |
|:--|:--|
| **An evolving personal assistant inside OpenClaw** | Groceries, Doctor and records are current examples; the purpose includes organizing information, ideas and projects with less context switching. |
| **“Less to juggle. More room to think and get things done.”** | The approved tagline connects attention with follow-through. |
| **A short README; depth in the chat guide and case study** | Readers can see the work quickly, then choose where to explore. Use tables for comparisons and navigation. |
| **Dark PNG chat cards with Geist typography** | Visible bubbles, media previews and transcripts make the examples readable. Keep Portuguese and English naturally. |
| **Illustrative media, clearly labeled** | Photo/video thumbnails and waveforms show the interaction; full dialogue remains available as text. |
| **Current use stated precisely** | Grocery is in use by three invited people; Doctor is in early testing. Personal emails and form filling with life-index context are owner-reported use. |
| **Stable, descriptive assets; replace superseded versions** | Give materially changed illustrations a new filename to avoid stale image URLs. Keep historical versions in Git. |

OpenClaw supplies the gateway, runtime and plugin ecosystem. Life-index supplies
read-only context; the personal agent's other tools perform actions. Keep that
contribution and boundary clear without repeating technical detail in every chat.

## Current assets

| Asset | Where it belongs |
|:--|:--|
| [Header](../assets/brand/readme-header-less-to-juggle.svg) · [Mark](../assets/brand/shadowos-mark.svg) | Project identity |
| [Groceries PNG](../assets/brand/chat-groceries-multimodal.png) | README: video, voice and a correction |
| [Doctor PNG](../assets/brand/chat-doctor-voice.png) | README: voice intake and confirmed search |
| [Support PNG](../assets/brand/chat-support-photo.png) | README: photo, purchase context, email and follow-up |
| [Receipt PNG](../assets/brand/chat-records.png) | Linked example: payment evidence and a reminder |
| [Grocery workflow](../assets/brand/workflow.svg) | Reference: media and instructions become shared state |

[Editable SVG sources](../assets/brand/source) are the source of truth. Exported
SVGs contain font outlines; PNGs are rendered from those exports at 2× resolution.
The README displays chat cards at 500px wide. Alt text and linked
[transcripts](CHAT-EXAMPLES.md) keep their meaning accessible outside the images.

## Visual conventions

| Element | Convention |
|:--|:--|
| Header | 1200 × 320; background `#171D23`; 72px wordmark, 38px tagline |
| Chat cards | 720px wide; 30px dialogue; regular 400 and medium 500 Geist |
| Chat surfaces | Background `#141D24`; incoming `#222D36`; outgoing `#263D39` |
| Chat text | Primary `#E7ECEF`; secondary `#AAB8C2`; mint accent `#B8D9CC` |
| Media | Static illustrated previews and waveforms, with readable transcripts |

Keep shapes flat and spacing open. GitHub controls the surrounding Markdown's
font and theme; the graphics keep their own opaque dark background.

Layout references: [Supabase](https://github.com/supabase/supabase),
[Hoppscotch](https://github.com/hoppscotch/hoppscotch) and
[Formbricks](https://github.com/formbricks/formbricks). Their clear identity,
short introductions and separate examples informed the hierarchy; no reference
artwork is bundled here.

## Edit and rebuild

1. Edit the matching SVG in `assets/brand/source/`. Text positions and line
   breaks are explicit; there is no automatic wrapping.
2. With Python 3.9+ and the HarfBuzz, librsvg, Cairo and GObject shared libraries
   available, run from the repository root:

   ```bash
   python3 -m venv /tmp/shadowos-brand-venv
   /tmp/shadowos-brand-venv/bin/python -m pip install -r assets/brand/requirements.txt
   /tmp/shadowos-brand-venv/bin/python assets/brand/build.py
   /tmp/shadowos-brand-venv/bin/python assets/brand/render_png.py
   ```

   `build.py` outlines every source SVG and reports text widths as JSON.
   `render_png.py` writes `chat-*.png`. These are optional graphics dependencies.
3. Check changed images at native size and 343px wide, including bubble padding,
   line breaks and accented characters. Update alt text and transcript links.
4. For a new filename, rename the source and export together, update references
   and remove the superseded files. Commit sources, exports and matching docs.

## Licensing and font provenance

Artwork and build scripts use the repository's [MIT license](../LICENSE).
The bundled Geist fonts use [SIL OFL 1.1](../assets/brand/fonts/OFL.txt).
Unmodified Regular and Medium TTFs come from
[Geist commit 10dc765](https://github.com/vercel/geist-font/tree/10dc7658f13c38a474cde201bb09a4617267545b/fonts/Geist/ttf).
The license is from the same revision, with trailing whitespace removed.
Copyright 2024 The Geist Project Authors.

| Font | SHA-256 |
|:--|:--|
| `Geist-Regular.ttf` | `85a1c6b18a6b0a06dfe9fd4f6d6a5d4979f74ec861eaef4bc7868b5492b8a117` |
| `Geist-Medium.ttf` | `3a3b36f0d0b981f4857f7f00eeef4a5ee123605575d362ab31ee7c19e3d11f2f` |
