---
name: Sorted Place
description: An accession register for Dublin's live job openings — a steel drawer of manila record cards that nothing is ever thrown out of.
colors:
  steel-900: "#171d20"
  steel-800: "#212a2e"
  steel-700: "#2f383c"
  steel-600: "#3c474c"
  steel-500: "#55636a"
  steel-400: "#adbac1"
  manila: "#ddcfae"
  manila-hi: "#e3d6b4"
  manila-lo: "#c7b791"
  manila-edge: "#b3a37c"
  ink: "#231e17"
  ink-2: "#5f5648"
  ink-3: "#5d5444"
  stamp: "#b7332a"
  stamp-deep: "#8e2620"
  brass: "#b08d3f"
  brass-hi: "#d9bc72"
  brass-lo: "#7d6228"
  ledger: "#cfd9c2"
typography:
  display:
    fontFamily: "Archivo, 'Helvetica Neue', Helvetica, Arial, sans-serif"
    fontSize: "clamp(2.5rem, 6.4vw, 4.9rem)"
    fontWeight: 900
    lineHeight: 0.94
    letterSpacing: "-0.032em"
    fontVariation: "'wdth' 78"
  headline:
    fontFamily: "Archivo, 'Helvetica Neue', Helvetica, Arial, sans-serif"
    fontSize: "clamp(1.25rem, 2.4vw, 1.6rem)"
    fontWeight: 800
    lineHeight: 1.12
    letterSpacing: "-0.018em"
    fontVariation: "'wdth' 84"
  title:
    fontFamily: "Archivo, 'Helvetica Neue', Helvetica, Arial, sans-serif"
    fontSize: "1.06rem"
    fontWeight: 800
    lineHeight: 1.32
    letterSpacing: "-0.012em"
  body:
    fontFamily: "Archivo, 'Helvetica Neue', Helvetica, Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: "'Courier Prime', ui-monospace, monospace"
    fontSize: "11px"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "0.17em"
rounded:
  card: "2px"
  tab: "2px 12px 0 0"
  pill: "11px"
spacing:
  xs: "4px"
  sm: "7px"
  md: "14px"
  lg: "22px"
  xl: "34px"
components:
  button-primary:
    backgroundColor: "{colors.stamp}"
    textColor: "#ffffff"
    rounded: "{rounded.card}"
    padding: "13px 22px"
    typography: "{typography.label}"
  button-primary-hover:
    backgroundColor: "{colors.stamp-deep}"
    textColor: "#ffffff"
  button-primary-disabled:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
  button-outline:
    backgroundColor: "transparent"
    textColor: "{colors.stamp-deep}"
    rounded: "{rounded.card}"
    padding: "8px 14px"
  button-outline-hover:
    backgroundColor: "{colors.stamp}"
    textColor: "{colors.manila-hi}"
  record-card:
    backgroundColor: "{colors.manila}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "18px 22px 17px"
  record-card-alt:
    backgroundColor: "{colors.ledger}"
    textColor: "{colors.ink}"
  slip-card:
    backgroundColor: "{colors.manila-hi}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "26px 34px 30px"
  tab-divider:
    backgroundColor: "{colors.manila-lo}"
    textColor: "#4a4235"
    rounded: "{rounded.tab}"
    padding: "10px 16px 9px"
  tab-divider-checked:
    backgroundColor: "{colors.manila-hi}"
    textColor: "{colors.ink}"
  label-plate:
    backgroundColor: "{colors.brass}"
    textColor: "#120d02"
    rounded: "{rounded.card}"
    padding: "7px 18px 8px"
  stamp-mark:
    backgroundColor: "transparent"
    textColor: "{colors.stamp-deep}"
    rounded: "{rounded.card}"
    padding: "2px 7px 1px"
---

# Design System: Sorted Place

## Overview

**Creative North Star: "The Accession Register"**

This is a working library register rendered as an interface: an oxidised steel drawer,
manila record cards threaded on a brass rod, a brass label holder screwed to the front,
and a red stamp pad. The metaphor is not decoration. It was chosen because a register
never throws anything out, which is the same promise the crawler underneath already
makes — a role stays filed until two separate crawls confirm it is gone. When the world
and the mechanism are the same idea, every component has an obvious correct form.

The register is a **finite, countable, physically bounded** collection, and the system
is built to say so. The count is the headline, it sits in brass hardware rather than in
a stat tile, and every record carries a stamped date. Nothing here scrolls forever;
the page is a drawer with a bottom.

The register is the system's private logic, not its vocabulary. Visitors are job
hunting, not filing: the world shapes the cards, the tabs and the stamps, while the
words on them stay plain. An earlier build printed a serial number on every record and
called the results "records pulled"; both were removed for reading as a filing clerk's
interface rather than a job search.

The system refuses two things by name. It refuses the category default — the centred
hero, the wide search bar, three icon cards, a logo strip — because that arrangement is
what every job board already ships. And it refuses the warm-cream-and-serif rendition
that "archival" usually collapses into. Card stock is not paper: it is `#ddcfae` manila
with a visible tooth, sitting in a `#2f383c` steel cabinet, marked in rubber-stamp red.

**Key Characteristics:**
- Dark steel ground, light card stock — the content is always on a card, never loose on the page
- Every number on screen is queryable from the database; nothing is rounded up for effect
- Type is one variable grotesque plus one typewriter face, and the typewriter face means *record*, never *technical*
- Motion is one grammar: things rise out of the drawer
- Hardware is modelled (screws, rod, pull), surfaces are flat

## Colors

A cabinet palette: cold rolled steel against warm card stock, with red ink and brass as
the only two saturated voices.

### Primary
- **Stamp Red** (`#b7332a`): the rubber stamp pad. Primary actions, the REQUIRED mark, the count of new records, the caret and text selection. It is ink struck onto a card, so it appears in small, dense marks rather than large fields.
- **Stamp Red Deep** (`#8e2620`): the same ink read at text size. Every stamp *outline*, every outline button label, every link inside card stock. Stamp Red itself does not clear 4.5:1 on manila; this does.

### Secondary
- **Brass** (`#b08d3f`), **Brass Light** (`#d9bc72`), **Brass Dark** (`#7d6228`): hardware only — the label holder, the rod through the cards, the drawer pull, the selected-tab shoulder, the focus ring. Brass never carries body copy on steel; Brass Light does, at 6.5:1.

### Neutral
- **Steel 700** (`#2f383c`): the drawer face, and the page ground everywhere.
- **Steel 800** (`#212a2e`) / **Steel 900** (`#171d20`): the recess above the fold and the machined lip under every rail.
- **Steel 600** (`#3c474c`): raised chrome — rails, the guide card, the pulling panel.
- **Steel 400** (`#adbac1`): all body copy that sits directly on steel. 6.03:1 on Steel 700.
- **Manila** (`#ddcfae`): standard card stock. Every filed record.
- **Manila Light** (`#e3d6b4`): the raised card — the request slip and the results header, the cards standing proud of the drawer.
- **Manila Low** (`#c7b791`): unselected tab dividers and file-input buttons.
- **Manila Edge** (`#b3a37c`): the cut edge of every card, carried as the 1px offset in both card shadows.
- **Ink** (`#231e17`): typewriter black. All primary text on card stock, 10.7:1.
- **Ink 2** (`#5f5648`) / **Ink 3** (`#5d5444`): secondary copy and typed micro-labels.
- **Ledger Green** (`#cfd9c2`): the tint band on alternate records. It is a banding device, never a status.

### Named Rules

**The Card Stock Rule.** Content sits on a card; chrome sits on steel. If something is
readable text and it is not on manila, it is on steel and it takes Steel 400 or lighter —
never Ink, which vanishes there. This rule exists because breaking it once made every
job title on the site invisible.

**The Never Cream Rule.** No surface goes lighter than `#e3d6b4`. Cream, parchment and
paper-white are the default this world was chosen against; the stock is dyed card, and
at `#ece2c9` it stops reading as one.

**The Two Saturated Voices Rule.** Red is ink and brass is metal. Red never appears as a
large field and brass never carries a paragraph. Everything else is steel or stock.

## Typography

**Display Font:** Archivo (variable, wdth 62–125, wght 400–900), with Helvetica Neue
**Body Font:** Archivo
**Label/Mono Font:** Courier Prime (400/700)

**Character:** One workhorse grotesque doing all the structural work, narrowed hard at
display sizes (`wdth 78`, `weight 900`) so headlines read like drawer-front signage
rather than like a marketing hero. Beside it, a real typewriter face carrying exactly
what a typewriter typed on a catalogue card: numbers, dates, field names, stamps.

### Hierarchy
- **Display** (900, `clamp(2.5rem, 6.4vw, 4.9rem)`, 0.94): page headline, engraved on the steel. Always on a drawer front, never on a card.
- **Headline** (800, `clamp(1.25rem, 2.4vw, 1.6rem)`, 1.12): section and card headings.
- **Title** (800, 1.06rem, 1.32): the job title on a record — the largest thing on any card.
- **Body** (400, 16px, 1.55): running copy, capped at 68ch (`--measure`), 58–60ch for ledes.
- **Label** (700, 11px, 0.17em, uppercase, Courier Prime): field names, dates, stamps, counts.

### Named Rules

**The Typewriter Means Record Rule.** Courier Prime appears only where a register would
actually have been typed: dates, counts, field names, stamp text. It
never appears to make something look technical, and never sets a sentence longer than a
line.

**The 11px Floor Rule.** No functional text ships below 11px, footers and micro-labels
included, and anything carrying a whole sentence goes to 12px.

**The Plain Words Rule.** The world is a register; the copy is not. Nothing on screen
asks a visitor to learn the metaphor to use the product: no "records pulled", no serial
numbers, no "drawer 3 of 10". Name what the thing is in the words someone job hunting
would use.

**The Short Caps Rule.** Uppercase is for labels of roughly 21 characters or fewer. The
24 role tabs were set in caps once; at 33 characters they scanned badly and failed
contrast, and mixed case fixed both. The tab *shape* says "tab", not the capitals.

## Layout

A single 1180px container with 24px gutters, dropping to 16px under 860px. The page is
a vertical stack of cards on a steel ground — there is no multi-column page grid, and
content columns only appear *inside* a card (`.grid2`, two equal columns collapsing to
one at 860px).

The drawer front (page heading, lede, ledger line) is a two-column grid at
`1.35fr / 0.65fr`: copy left, a proof card right, collapsing to one column at 980px.

Spacing rhythm runs 4 / 7 / 14 / 22 / 34px. Headings carry more space above than below.
Filed records sit in a `.filed` column with 34px of left padding — that gutter is where
the brass rod runs, and the rod and its notches are dropped entirely under 860px, where
the padding would cost more than the metaphor returns.

Breakpoints: 980px (drawer front unstacks), 860px (the main phone/desktop switch), 420px
(label plate compaction).

**The Drawer Has A Bottom Rule.** Any list that can exceed ~50 rows pages. Results page
at 25, the employer register at 50. This is load-bearing: shipping the register
unpaginated produced a 76,489px page on a phone.

## Elevation & Depth

A hybrid. Surfaces are flat and matte; *hardware* is modelled. Cards carry a two-part
shadow whose first component is a 1px hard offset in Manila Edge — that is the cut edge
of the card stock, not a neobrutalist block shadow — followed by a soft, offset ambient
shadow. Brass objects carry inset highlight and shade to read as turned metal.

### Shadow Vocabulary
- **Card** (`0 1px 0 #b3a37c, 0 6px 14px -6px rgba(0,0,0,.55)`): every filed record and standard card.
- **Raised** (`0 2px 0 #b3a37c, 0 22px 38px -16px rgba(0,0,0,.7)`): cards standing proud of the drawer — the request slip, the proof card.
- **Hardware inset** (`0 1px 0 rgba(255,255,255,.45) inset, 0 -1px 0 rgba(0,0,0,.35) inset`): the label plate and drawer pull.
- **Notch** (`inset 0 1px 2px rgba(0,0,0,.7)`): the hole where the rod passes through a card.

**The Cut Edge Rule.** A card's hard 1px offset is always paired with a soft blurred
shadow and is always Manila Edge. A zero-blur coloured shadow on its own is a costume
this world did not choose.

## Shapes

Near-square. The card radius is 2px throughout — card stock is guillotined, not rounded.
The single exception is the tab shoulder: guide cards and role tabs take `2px 12px 0 0`,
an asymmetric corner that reads as the angled shoulder of a filing tab, and the drawer
pull takes a full 11px pill because it is turned brass.

Borders are hairlines (`rgba(35,30,23,.22)` on stock, `rgba(255,255,255,.09)` on steel)
and act as ruled index lines, not as containers. Inputs have no box: they are a single
1.5px baseline rule under the value, which goes Stamp Red on focus.

**The Ruled Line Rule.** Separation is a ruled hairline or a change of stock colour.
It is never a box inside a box; nested cards do not exist in this system.

## Components

### Buttons
- **Shape:** effectively square (2px radius).
- **Primary:** Stamp Red field, white label, 13px/800 uppercase at 0.13em, 13px × 22px padding, with a 1px Stamp Deep offset under it so it sits like a struck stamp.
- **Hover / Focus:** darkens to Stamp Deep and lifts 1px; active presses 1px down and drops the offset. Focus is a 2px Brass Light ring at 2px offset.
- **Disabled:** loses its field entirely and becomes a dashed 1.5px outline with Ink 2 text — an unstruck stamp, still legible.
- **Outline:** transparent with a 1.5px Stamp Deep edge, inverting to a filled stamp on hover. Used for every action that leaves this site.
- **Link-ish:** underlined Stamp Deep text, for destructive or undo actions that must be POSTs.

### Chips — role tabs (signature)
- **Style:** Manila Low field, 1px Manila Edge, no bottom border, `2px 12px 0 0` shoulder, 13px/700 mixed case. The whole group stands on a 2px Manila Edge rule.
- **State:** hover lifts 2px and warms to Manila; checked raises 6px, brightens to Manila Light, takes a Brass border and lays a 3px brass bar along its bottom edge. Selection is conveyed by *elevation*, not by a checkmark.

### Cards / Containers
- **Corner:** 2px.
- **Background:** Manila standard, Manila Light when raised, Ledger Green on alternate records.
- **Shadow:** Card, or Raised when the card stands proud.
- **Texture:** two radial-gradient fibre specks at 7px and 11px — visible tooth at reading distance, not a pattern.
- **Padding:** 26px/28px standard, 26px/34px raised, 18px/22px on a record, tightening to 13px/16px under 860px.

### Inputs / Fields
- **Style:** no box. A translucent white wash (46%) under the value and a 1.5px Rule Strong baseline.
- **Focus:** the wash goes to 72% and the baseline goes Stamp Red; the caret is Stamp Red everywhere.
- **File input:** dashed baseline with a Manila Low button styled as a 11.5px uppercase label.
- **Select:** an inline SVG chevron in Ink 2; no native arrow.

### Navigation
- 11.5px/700 uppercase at 0.14em in Steel 400, on the steel rail. Hover lifts to Manila Light over a 5% white wash; the current page takes Manila Light plus a 2px brass underline. Under 860px the rail becomes a horizontally scrolling strip with its scrollbar hidden.

### The Label Holder (signature)
The brass plate screwed to the drawer front. A three-stop brass gradient with two
radial-gradient screw heads, carrying the lowercase wordmark and, in Courier Prime, the
live count of what is in the drawer. On load the count stamps up from zero over 900ms.
It is the site's masthead, its counter and its status line in one object.

### The Rod (signature)
Filed records sit in a 34px left gutter holding a 4px brass rod with a rounded bead at
its foot. Each record punches a 12px steel-filled notch where the rod passes through it.
Dropped entirely under 860px.

## Do's and Don'ts

### Do:
- **Do** put readable content on card stock and chrome on steel, and check the text colour against the ground it actually lands on.
- **Do** state every number literally, from the database, including the unflattering ones — the register's whole claim is that its counts are checkable.
- **Do** convey selection with elevation and hardware (the tab raises, brass appears), not with ticks or colour fills.
- **Do** keep motion to the one grammar: things rise out of the drawer, on `cubic-bezier(.16,.84,.34,1)`, from a state that is already visible without JavaScript.
- **Do** cap the stagger on entrance animations (14 steps × 38ms) so a hundred records never take ten seconds to arrive.
- **Do** page any list that can exceed ~50 rows.
- **Do** theme the browser's own surfaces — selection, caret, scrollbar, focus ring, tabular numerals.

### Don't:
- **Don't** go lighter than `#e3d6b4` on any surface, and never reach for cream, parchment or a display serif.
- **Don't** set a passage longer than ~21 characters in uppercase.
- **Don't** use Courier Prime to make something look technical; it means *typed record*.
- **Don't** put a coloured accent border on the side of a card, callout or alert — the brass belongs on a tab shoulder or a rod.
- **Don't** nest a card inside a card, or use a box where a ruled hairline will separate.
- **Don't** let red carry a large field or brass carry a paragraph.
- **Don't** claim a capability the product lacks. There is no alerting of any kind; "everything in one place" means the complete live list.
