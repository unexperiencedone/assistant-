---
name: Nova
description: Manga x HUD. Nova is a manga character who works inside your screen, inked on manuscript paper.
colors:
  paper: "#f5f5f2"
  paper-shade: "#e9e9e4"
  sheet-white: "#ffffff"
  pen: "#0d0d0d"
  pen-grey: "#3b3b3b"
  body-ink: "#1b1b1b"
  faint-graphite: "#6a6a66"
  guide-blue: "#a9c8e8"
  spot-pink: "#ff1f6f"
  failed-red: "#b3001f"
typography:
  sfx:
    fontFamily: "Dela Gothic One, Rozha One, sans-serif"
    fontSize: "22px"
    fontWeight: 400
    lineHeight: 1
  headline:
    fontFamily: "Zen Kaku Gothic New, Mukta, system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 900
    lineHeight: 1
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Zen Kaku Gothic New, Mukta, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 900
    lineHeight: 1.25
    letterSpacing: "-0.01em"
  said:
    fontFamily: "Zen Kaku Gothic New, Mukta, system-ui, sans-serif"
    fontSize: "15.5px"
    fontWeight: 700
    lineHeight: 1.55
  body:
    fontFamily: "Zen Kaku Gothic New, Mukta, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Zen Kaku Gothic New, Mukta, system-ui, sans-serif"
    fontSize: "11.5px"
    fontWeight: 900
    lineHeight: 1
    letterSpacing: "0.08em"
  pencil:
    fontFamily: "Klee One, Mukta, cursive"
    fontSize: "18px"
    fontWeight: 600
  devanagari-head:
    fontFamily: "Rozha One, serif"
    fontSize: "21px"
    fontWeight: 400
    lineHeight: 1
  devanagari-sub:
    fontFamily: "Mukta, sans-serif"
    fontSize: "12.5px"
    fontWeight: 700
rounded:
  frame: "0px"
  balloon: "26px"
  orb: "50%"
spacing:
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "20px"
  gutter: "32px"
components:
  button:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.pen}"
    typography: "{typography.title}"
    rounded: "{rounded.frame}"
    padding: "6px 12px"
  button-hover:
    backgroundColor: "{colors.pen}"
    textColor: "{colors.paper}"
  button-spot:
    backgroundColor: "{colors.spot-pink}"
    textColor: "{colors.pen}"
    rounded: "{rounded.frame}"
    padding: "6px 12px"
  button-spot-hover:
    backgroundColor: "{colors.pen}"
    textColor: "{colors.spot-pink}"
  key:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.pen}"
    rounded: "{rounded.frame}"
    height: "24px"
    padding: "0 6px"
  stamp:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.pen}"
    typography: "{typography.label}"
    rounded: "{rounded.frame}"
    padding: "3px 8px 4px"
  stamp-solid:
    backgroundColor: "{colors.pen}"
    textColor: "{colors.paper}"
  caption:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.pen}"
    rounded: "{rounded.frame}"
    padding: "12px 14px 13px"
  caption-tab:
    backgroundColor: "{colors.pen}"
    textColor: "{colors.paper}"
    typography: "{typography.label}"
    padding: "4px 8px 5px"
  balloon:
    backgroundColor: "{colors.sheet-white}"
    textColor: "{colors.pen}"
    typography: "{typography.said}"
    rounded: "{rounded.balloon}"
    padding: "14px 20px 16px"
  choice:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.pen}"
    rounded: "{rounded.frame}"
    padding: "8px 14px"
  choice-first:
    backgroundColor: "{colors.spot-pink}"
    textColor: "{colors.pen}"
  count-badge:
    backgroundColor: "{colors.spot-pink}"
    textColor: "{colors.pen}"
    size: "21px"
---

# Design System: Nova

## Overview

**Creative North Star: "The Character on Your Page"**

Nova is drawn, not rendered. Every surface is a page of manuscript: off-white paper, G-pen black ink, a non-repro blue underdrawing that structure hangs from, and exactly one spot ink, pink, for what happens next or what is live right now. Panels are square ink frames whose stroke weight carries hierarchy; the only curves on the page are the speech balloon and Nova's screentone orb, because those are the parts that speak and breathe. The HUD half of the world contributes mechanics, not chrome: keycaps for the four spaces (1-4) and the first three decisions (F1-F3), numbered spoken choices, and ink lock brackets that close onto what Nova read.

The canvas and the overlay are the same page. Opened in its window, Nova's page splits into the conversation (7/12) and the quests waiting on the owner (5/12), with today as a single inked timeline along the bottom gutter. Summoned over another app, the same grammar appears as one tilted splash panel over a dimmed still of the screen: portrait with nameplate, the heard question as a caption, the answer as a balloon, numbered choices with the first in spot. Density is moderate and reading-first; paper stays visible around every frame.

Every reading surface is bilingual by construction: an English line in Zen Kaku Gothic New with a Devanagari companion in Rozha One or Mukta, on nav items, section heads and caption tabs. The world draws its SFX words (Dela Gothic One, paper-outlined) from the real status slice: "listening...", "thinking...", "GOT IT!".

**Key Characteristics:**
- Paper and ink, light scheme only (`color-scheme: light`); depth is frame weight and underdrawing, never shadow.
- Square frames at 2 / 3 / 5px; balloons (26px) and the orb (circle) are the only curves.
- One spot pink, used as a fill under ink: next action, live state, now.
- Screentone dots (radial-gradient dot grids) for tone; no airbrushed gradients.
- Keycaps and numbered choices mirror the voice path; every button has a spoken twin.
- English paired with Devanagari on navigation, headings and caption tabs.

## Colors

Monochrome manuscript with one spot ink and one non-printing structural blue.

### Primary
- **Spot Pink** (spot-pink): the single accent. Fills the primary action of each quest, the first overlay choice, the send button, the live orb, the Needs-you count badge, the timeline's now tick, the active-space underline, link underlines, the selection highlight, and the 3px focus outline. Always carries ink text or ink outline on top of it.

### Secondary
- **Guide Blue** (guide-blue): the non-repro pencil of a manuscript page. Two fixed 1px margin rules 12px inside the window edges, the rough underdrawing offset behind every panel (1.5px, rotated -0.4deg, 65% opacity), and the legacy grid tint (`rgba(169,200,232,.45)`). Structure only; never text, never a fill, never a state.

### Neutral
- **Manuscript Paper** (paper): the ground of every surface, every panel interior, every caption, every keycap.
- **Paper Shade** (paper-shade): hover fill on legacy list rows (`--panel-hover`).
- **Sheet White** (sheet-white): the inside of speech balloons and the sheets that hold read-out text (quest detail, schedule slots, overlay ask field, legacy assistant messages). White means "this is what was said or written".
- **G-Pen Ink** (pen): every frame, all primary text, solid stamps, caption tabs, the nameplate, screentone dots.
- **Pen Grey** (pen-grey): secondary text: context lines, small print, Devanagari nav subs, placeholders, empty states.
- **Body Ink** (body-ink): legacy body text re-point (`--body`).
- **Faint Graphite** (faint-graphite): disabled button text and pending status.
- **Failed Red** (failed-red): failure only, on paper: failed schedule slots and the dashed danger button. Its tint `rgba(179,0,31,.08)` backs legacy failed rows.

Legacy status tokens re-pointed by the world: running `#8a0b3c` on `rgba(255,31,111,.1)`, done ink on `rgba(13,13,13,.06)`, `--violet` collapsed to ink, `--accent-soft` `#c4004f`. They exist so older screens read on paper; new surfaces use the spot/ink pair instead.

### Named Rules
**The One Spot Rule.** Pink marks the next action or the live state, one per cluster: the primary button of a quest, the first of the choices, the now tick, the live orb. A second pink element in the same panel means one of them is lying.

**The Spot Is a Fill Rule.** Pink sits under ink, never as text on paper (pink on paper is about 3.4:1). Where pink text appears it is on ink (the spot button's hover).

**The Non-Repro Rule.** Guide blue draws structure that a printer would drop: margins and underdrawing. If removing it loses information, it was the wrong color.

## Typography

**Display Font:** Dela Gothic One (with Rozha One, sans-serif)
**Body Font:** Zen Kaku Gothic New (with Mukta, system-ui, sans-serif)
**Pencil Font:** Klee One (with Mukta, cursive)
**Devanagari:** Rozha One for headings and `[lang="hi"]`, Mukta (500 and 800 files) for nav subs and tabs

**Character:** A heavy poster gothic shouts the sound effects; a clean Japanese gothic at 700-900 does the reading, set bold enough to hold up against 3px frames; a handwritten pencil adds margin notes. All faces are self-hosted from `canvas/public/fonts`, Devanagari subsets by unicode-range.

### Hierarchy
- **SFX** (400, 22px, line-height 1): status words in the masthead and the overlay portrait (18px there, rotated -5deg). Outlined in paper (`-webkit-text-stroke: .16em`, `paint-order: stroke fill`) so it reads over tone and frames. The brand wordmark uses the same face at 24px.
- **Headline** (900, 24px, 1, -0.02em): section heads ("Needs you"), always followed by a Devanagari line in Rozha One 21px on the same baseline.
- **Title** (900, 17px, 1.25, -0.01em): quest titles; the overlay tracker head at 18px; running-task and arm names at 14-15px.
- **Said** (700, 15.5px, 1.55): Nova's words inside balloons and the owner's words inside captions. Speech is set bold.
- **Body** (400, 15px, 1.55): base text; context lines at 13px 700 in pen grey.
- **Label** (900, 11.5px, 0.08em, uppercase): caption tabs and ledger column heads; stamps at 11px 0.07em.
- **Pencil** (Klee One 600, 18px): spot-backed margin notes; 15px for the "say 'one'" hint and the quest's said-back line.

### Named Rules
**The Bilingual Line Rule.** Navigation, section heads and caption tabs carry an English line and a Devanagari companion (`lang="hi"`). The Devanagari is a second voice, never a translation table.

**The Speech Is Bold Rule.** Anything spoken, by Nova or by the owner, is set at 700. Regular weight is for context and metadata.

## Layout

Full-window, desktop only, no breakpoints. A masthead (12px 32px 10px, 3px ink rule below) holds brand, the four spaces keyed 1-4, Briefing, the live line, the SFX status and the orb. Content sits inside 32px horizontal gutters; two fixed guide-blue rules run 12px inside the window edges.

- **Now:** grid `minmax(0,7fr) minmax(320px,5fr)`, 16px gap, padding 20px 32px 16px. Left: conversation splash panel over the compose panel (14px gap). Right: the Needs-you rail (14px gap, scrolls). Bottom row spans both: a 300px running panel beside an inked timeline (3px rule, ticks positioned by time, clamped 2-98%).
- **Studio / You / Work:** a scrolling space, padding 20px 32px 24px; the week is 7 equal columns (10px gap, 118px min day height); side-by-side content uses `minmax(0,1fr) 380px`, 16px gap.
- **Overlay:** the monitor still fills the window under a 22% ink dim; the answer panel is centered `min(940px, 100% - 48px)`, 40px from the bottom (or top when the lock sits low), max 70vh, with a 124px portrait column. The Needs-you tracker (320px) sits 28px from the top right, moving left when the lock is there.

Rhythm: 8 / 12 / 16 / 20 / 32. Panels pad 12-16px; conversation 20px 24px.

## Elevation & Depth

Flat. No box-shadows anywhere on the page; legacy shadows are explicitly reset to none. Depth is drawn: frame weight says importance (2px inset, 3px panel, 5px splash), the guide-blue underdrawing offset behind each panel says "this was sketched first", and small rotations (-0.4deg panel, 0.5deg tracker, -2deg stamps and notes) say "this was pasted on". Screentone dot grids (`radial-gradient(ink .95px, transparent 1.15px)` at 6px) supply tone in progress bars and on the orb.

The single box-shadow in the system is functional: the overlay lock brackets carry a 2px paper halo (`0 0 0 2px paper, inset 0 0 0 2px paper`) so ink stays legible over any app's pixels.

### Named Rules
**The Weight Not Lift Rule.** To raise importance, thicken the frame (3px to 5px) or fill it; never add a shadow or a radius.

**The Screentone Rule.** Tone is dots of ink. Gradients appear only as dot grids and the orb's white highlight, never as a color blend.

## Shapes

Every corner is square (0px), enforced even on legacy cards, buttons and inputs inside a space. Two shapes curve: the speech balloon (26px radius, 3px ink border, white) and the orb (a circle, 3px ink border, 5px screentone, white catch-light at 34% 30%; Work's entity is the same disc at 5px). Diamonds (12px squares rotated 45deg, 2.5px ink) mark objectives: empty pending, spot live, ink done; the same diamond at 9px is the disclosure marker. Dashed borders mean "not yet": scheduled slots, scheduled and automation stamps, disabled buttons, the talk-mode stamp. Keycaps are 2px boxes with a 4px bottom edge.

## Components

### Buttons
Blunt ink boxes that invert on contact.
- **Shape:** square (0px), 2px ink border.
- **Default:** paper fill, ink text, Zen Kaku 900 13.5px, padding 6px 12px, 8px gap to an inline keycap.
- **Hover:** inverts to ink fill, paper text (120ms).
- **Spot:** the primary action; spot fill, 3px border; hover goes ink fill with pink text.
- **Disabled:** dashed border, faint graphite text, not-allowed cursor.
- **Focus:** global 3px spot outline, 3px offset.

### Keycaps
Little printed keys (24px tall, 2px border, 4px bottom border, 900 12px): 1-4 on the space nav, F1-F3 on the first three quests' primary action (20px inside buttons), 1-3 on overlay choices, Esc on the ask field. A keycap only appears where the key actually works.

### Stamps
Rubber stamps (3px border, 900 11px uppercase, rotated -2deg): outline for a tag, solid ink for a count ("2 of 5"), dashed for anything scheduled or proposed. They sit on the title row of a quest.

### Captions and Tabs
The owner's words, standing goals and "what Nova read" are captions: 2px square ink boxes on paper. A caption tab (ink block, paper uppercase label, pinned above the box's top-left corner) names the speaker or source with its Devanagari twin: "You · आप", "Looking at · नज़र में", "Nova is reading". A tab attributes a caption; it never labels a section.

### Balloons
Nova's speech only: white, 3px ink, 26px radius, said type, beside a 40px orb. Nothing else on the page is round-cornered.

### Inputs / Fields
Borderless input inside an ink frame: the compose bar is a 3px panel with a 44px spot send square (SVG arrow, square caps); the overlay ask field is 3px ink on white with an Esc keycap. Text is 700 16px; placeholder pen grey; focus is the global spot outline.

### Navigation
The masthead's four spaces: keycap, English label (700 15px) over a Devanagari sub (Mukta 700 12.5px). The current space underlines its label 3px in spot (offset 5px). Now carries the spot count badge (21px, 2px ink) for decisions waiting. Sub-tabs within a space are 2px boxes that fill ink when pressed.

### Quests (signature)
One panel per decision waiting on the owner: title row with stamp, context line, a "Read it" disclosure opening a white sheet (max 160px, scrolls), then actions with the primary in spot and its F-key. The reply appears in pencil under the actions. The overlay tracker repeats the first three as diamonds, the first marked live.

### The Lock (signature)
Four ink corner brackets (5px stroke, arm 10-30px scaled to the target, padded to at least 44px) ink onto the region Nova read with a clip-path wipe (300ms, staggered 40ms per corner), followed by the answer panel rising (460ms, 260ms delay, settling at -0.4deg). A caption tab below the lock names what is being read. Both motions use `cubic-bezier(.16,1,.3,1)` and only run without reduced motion.

### The Orb
Nova's portrait, sized 36px (masthead), 40px (conversation), 70px (overlay). Paper screentone at rest; spot screentone when Nova is busy, breathing (1.6s, scale 1.08, rotate 6deg) only when motion is allowed. Driven by the status slice, never a loop for its own sake.

## Do's and Don'ts

### Do:
- **Do** express hierarchy with frame weight: 2px inset, 3px panel, 5px splash (conversation, overlay panel, today's day cell).
- **Do** keep spot pink to one live or next-action element per cluster, always with ink on or around it.
- **Do** pair every nav item, section head and caption tab with a Devanagari line (`lang="hi"`).
- **Do** give every primary action a key or a spoken number, and show the keycap only where the key works.
- **Do** use dashed borders for anything scheduled, proposed or unavailable.
- **Do** guard all motion with `prefers-reduced-motion: no-preference`, using `cubic-bezier(.16,1,.3,1)`.
- **Do** self-host every face in `canvas/public/fonts` so the page looks the same offline.

### Don't:
- **Don't** add box-shadows or rounded corners to frames, cards, buttons or inputs; curves belong to balloons and the orb.
- **Don't** set pink text on paper, or use guide blue for anything but structure.
- **Don't** blend colors in gradients; tone is screentone dots.
- **Don't** put a caption tab or uppercase tracked label above a section heading as decoration; tabs attribute speech and sources.
- **Don't** animate the orb or entity without a real status behind it.
- **Don't** design new surfaces in the legacy Living Ink vocabulary (Fraunces, Plex Mono, coral, violet, dark ground); `styles.css` is legacy that `manga.css` overrides.
