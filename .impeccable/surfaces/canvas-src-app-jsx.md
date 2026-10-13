---
version: 1
slug: "canvas-src-app-jsx"
primary_target: "canvas/src/App.jsx"
related_targets: ["docs/mockups/variants/e-manga-hud.html"]
---

# Surface: Nova canvas and in-place overlay

Scope: the canvas (four spaces: Now, Work, Studio, You) and the overlay Nova draws over any
app when summoned. Visitor mode: Operate. Primary target `canvas/src/App.jsx`; mockup
`docs/mockups/variants/e-manga-hud.html`.

Job: decide fast, see what Nova is doing, talk with it, recall his own stuff — reachable in
place over whatever app he is in. Summoned, not parked. Desktop only.

## Direction contract

THESIS: Nova is a manga character who works inside your screen. It refuses the category's
floating chat window and command palette: help arrives as an inked panel over the app you are
in, pointing at what it read, ending in choices you can say out loud.

OWN-WORLD: manuscript paper (#f5f5f2), G-pen ink (#0d0d0d), non-repro guide blue (#a9c8e8)
for structure only, one spot pink (#ff1f6f) for the next action and live state. Square
frames at 2 / 3 / 5px over a rough blue underdrawing; speech balloons are the only curves,
because Nova speaks. Dela Gothic One for SFX beats, Zen Kaku Gothic New for reading, Klee
pencil notes, Rozha and Mukta for Devanagari answering lines. Screentone, never gradients.

STORY: he asks about what is on screen; ink brackets lock onto exactly what Nova read; a
panel answers in a balloon and ends in numbered choices (say "one"); pending decisions wait
as quests in a corner tracker; the canvas is the same page, opened up.

FIRST VIEWPORT: overlay — ink lock brackets on the selection with a caption tab naming it;
a bottom panel: inked orb portrait with nameplate, the heard question as a caption, the
answer balloon, three numbered choice rows, choice one in spot. Top right: Needs-you quest
tracker panel. Canvas — masthead with chapter nav keyed 1–4, conversation as panels and
balloons (left 7/12), quests with F-keys (right 5/12), today as an inked timeline gutter.

FORM: fusion of grounded candidate 5 (manga panels, the roll's assignment) with candidate 1
(game HUD, the pick) supplying mechanics only; seed key bd72412c.

Signature interaction: the lock — brackets ink onto the region Nova is reading (clip-path
wipe, 300ms) before the answer panel rises; choices respond to "one / two / three".

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
