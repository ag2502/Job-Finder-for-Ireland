---
name: Sorted Place
description: The tidy desktop. Job hunting is twenty careers sites open in twenty windows; Sorted Place is the one window that has all of them in it.
colors:
  desk: "#eceef2"
  desk-dot: "#c3c9d2"
  desk-deep: "#dfe3e9"
  win: "#ffffff"
  win-bar: "#f4f5f7"
  win-bar-2: "#e8eaee"
  line: "#dde1e7"
  line-strong: "#c4cad3"
  ink: "#15171c"
  ink-2: "#454b56"
  ink-3: "#5c626d"
  aqua: "#2b74f0"
  aqua-hi: "#69a4ff"
  aqua-deep: "#1a55c2"
  aqua-wash: "#edf3ff"
  sticker: "#ffd84a"
  sticker-deep: "#e8b400"
  tag-red: "#e8413c"
  tag-red-deep: "#c02c28"
  sorted: "#1c9a4a"
  sorted-wash: "#e6f6ec"
  tl-red: "#ff5f57"
  tl-amber: "#febc2e"
  tl-green: "#28c840"
  g-engineering: "#2b74f0"
  g-data-ai: "#9b51e0"
  g-product-design: "#e84393"
  g-science-health: "#0fa5a0"
  g-industry: "#f2791c"
  g-finance-legal: "#1c9a4a"
  g-commercial: "#e8413c"
  g-business-ops: "#5b5bd6"
  g-people: "#d9a300"
  g-public-education: "#9a6b3f"
  g-service: "#7c828d"
typography:
  display:
    fontFamily: "Geist, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "clamp(2.6rem, 6.4vw, 5rem)"
    fontWeight: 750
    lineHeight: 0.98
    letterSpacing: "-0.035em"
    fontFeature: "\"ss01\", \"tnum\""
  headline:
    fontFamily: "Geist, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "clamp(1.4rem, 2.6vw, 1.9rem)"
    fontWeight: 700
    lineHeight: 1.12
    letterSpacing: "-0.03em"
  title:
    fontFamily: "Geist, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "1.05rem"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.015em"
  lede:
    fontFamily: "Geist, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "1.14rem"
    fontWeight: 400
    lineHeight: 1.5
  body:
    fontFamily: "Geist, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.55
    fontFeature: "\"ss01\", \"tnum\""
  label:
    fontFamily: "Geist, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "13px"
    fontWeight: 650
    lineHeight: 1.3
  mono:
    fontFamily: "Geist Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12.5px"
    fontWeight: 400
    letterSpacing: "-0.01em"
  hand:
    fontFamily: "Gochi Hand, Comic Sans MS, cursive"
    fontSize: "20px"
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: "0.01em"
rounded:
  menu-item: "7px"
  control: "10px"
  window: "14px"
  dock: "24px"
  sky: "32px"
  app-icon: "24%"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "26px"
  2xl: "64px"
  menubar: "48px"
  container: "1180px"
components:
  button-primary:
    backgroundColor: "{colors.aqua}"
    textColor: "{colors.win}"
    typography: "{typography.body}"
    rounded: "{rounded.pill}"
    padding: "11px 22px"
  button-ghost:
    backgroundColor: "{colors.win}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "11px 22px"
  button-apply:
    backgroundColor: "{colors.win}"
    textColor: "{colors.aqua-deep}"
    rounded: "{rounded.pill}"
    padding: "7px 15px"
  button-apply-hover:
    backgroundColor: "{colors.aqua}"
    textColor: "{colors.win}"
  save-toggle:
    backgroundColor: "{colors.win}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    padding: "7px 13px"
  save-toggle-on:
    backgroundColor: "#fff4c7"
    textColor: "#7a5800"
  field-tag:
    backgroundColor: "{colors.win}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "7px 14px 7px 11px"
  input-text:
    backgroundColor: "{colors.win}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 13px"
  window:
    backgroundColor: "{colors.win}"
    textColor: "{colors.ink}"
    rounded: "{rounded.window}"
    padding: "26px 30px 30px"
  window-titlebar:
    backgroundColor: "{colors.win-bar}"
    textColor: "{colors.ink-3}"
    typography: "{typography.mono}"
    height: "38px"
    padding: "0 14px"
  job-row:
    backgroundColor: "{colors.win}"
    textColor: "{colors.ink}"
    padding: "19px 26px"
  status-pill:
    backgroundColor: "{colors.sorted-wash}"
    textColor: "{colors.sorted}"
    rounded: "{rounded.pill}"
    padding: "3px 9px"
  count-badge:
    backgroundColor: "{colors.tag-red}"
    textColor: "{colors.win}"
    rounded: "{rounded.pill}"
    height: "22px"
    padding: "0 6px"
  menubar:
    backgroundColor: "rgba(246,247,249,.78)"
    textColor: "{colors.ink-2}"
    height: "48px"
    padding: "0 18px"
---

# Design System: Sorted Place

## Overview

**Creative North Star: "The Tidy Desktop"**

The site is a light computer desktop. A cool grey ground with a faint dotted grid stands in for the screen; every piece of content that works (the find-my-jobs form, the results, the company list, a company's jobs, the privacy notes) sits in a white app window with a grey title bar, three traffic lights and a mono filename. Employers show up as app icons with their real favicons and a red notification badge carrying their open-job count. The home page acts out the premise: real employers' careers windows scattered across the desk, draggable and throwable, which one aqua button sorts into a single list below.

The light ground is chosen for how the site is actually used. Someone arrives mid-hunt on a laptop with twenty careers tabs open, or on a phone on a bus, and both need to read a long list in daylight. The desktop metaphor is literal enough to explain the product without copy ("close the other nineteen tabs"), and it stays out of the way once the visitor is working: from the first result onward the page is a plain white list inside a window.

Play lives at the edges: stickers, a highlighter, handwritten asides, a dock that swells under the pointer, windows that spring open. It never stands between the visitor and a job link. Every number on the desk is a real, live figure from the database, in keeping with PRODUCT.md's literal, checkable voice, and nothing here signals alerts or notifications. The red badge is a count, not something unread.

**Key Characteristics:**
- Light desktop ground (#eceef2, 22px dotted grid) under white windows; no dark surfaces except the dock tooltip.
- One action colour: glossy aqua pills. Green reads as "sorted / live". Red is kept to count badges and a few marks (applied, required, errors).
- Finder tag colours identify the eleven field groups, and that is their only job.
- Three typefaces with separate jobs: Geist carries the interface, Geist Mono types data, Gochi Hand scribbles asides.
- Springy for anything you touch, an exponential settle for anything that arrives; everything still works and stays visible with reduced motion or without JS.

## Colors

A cool, near-neutral greyscale desktop with one saturated blue for action and a small set of Mac system colours, each with a single meaning.

### Primary
- **Aqua** (`aqua`): the one colour that means "do this". Glossy primary pills, the Apply outline, focus rings, the text caret, input focus borders, the active step marker and progress bar in the how-it-works story, and the sky-blue platforms band on /companies. Buttons shade it from `aqua-hi`-adjacent #5c9bff at the top to #2166e0 at the base, edged with `aqua-deep`.
- **Deep Aqua** (`aqua-deep`): button edges, text links, the Apply label, sort-column active state. Aqua text is always this deeper value, never `aqua` itself, so it holds contrast on white.
- **Aqua Wash** (`aqua-wash`): hover fill on company rows and job links in company windows, info notices, CV-drop hover, extracted-skill chips.

### Secondary
- **Sorted Green** (`sorted`) and **Sorted Wash** (`sorted-wash`): "live and sorted". Open-in-Dublin counts on careers windows and company windows, default status pills, the loaded CV drop, completed story steps, the ready state of the form hint.
- **Traffic-light Green** (`tl-green`): the live dot in the menu bar and the on state of switches, as in macOS.

### Tertiary
- **Highlighter Yellow** (`sticker`, `sticker-deep`): the highlighter sweep under one phrase of a headline, text selection, sticky notes, and the saved state of the Save toggle (fill #fff4c7, label #7a5800, edge `sticker-deep`). The "stretch" pill uses the same pale yellow.
- **Tag Red** (`tag-red`, `tag-red-deep`): notification badges on app icons, the "applied" and "required" pills, the name-tag sticker header, error notices.

### Field-group tags
The eleven `g-*` colours are Finder tag colours, one per field group (engineering blue, data and AI purple, product and design pink, science and health teal, industry orange, finance and legal green, commercial red, business and operations indigo, people gold, public and education brown, service grey). Each group sets a local `--g` and every chip in it takes its dot, check fill, border and 11% tint from that value.

### Neutral
- **Desk Grey** (`desk`) with **Desk Dot** (`desk-dot`) grid points and **Desk Deep** (`desk-deep`): the page ground, also the browser theme colour.
- **Window White** (`win`), **Title-bar Grey** (`win-bar` to `win-bar-2` gradient): window bodies and title bars; `win-bar` also fills table heads, list column headers, footers of windows and the search field.
- **Hairline** (`line`) and **Strong Hairline** (`line-strong`): row dividers inside windows; control borders and story step rules.
- **Ink** (`ink`), **Ink 2** (`ink-2`), **Ink 3** (`ink-3`): headings and primary text; secondary copy, ledes, nav; mono metadata, employer names, filenames, placeholders are one step lighter (#7d838e).

### Named Rules
**The One Go Colour Rule.** Full-strength aqua marks what the visitor can do next: the primary pill, Apply, focus, progress. Nothing decorative is aqua, and no second hue competes with it for an action. The platforms band is the single large aqua field, and it holds draggable pills, not text to read at length.

**The Tag Is The Field Rule.** Finder tag colours belong to field groups and appear only on field chips and group labels. They are never used as decoration, status, or brand colour elsewhere.

**The Green Means Sorted Rule.** Green says a thing is live, done or ready (open counts, a read CV, a completed step, a switch that is on). It never labels an action.

## Typography

**Display / Body Font:** Geist (with ui-sans-serif, system-ui, -apple-system)
**Label/Mono Font:** Geist Mono (with ui-monospace, SFMono-Regular, Menlo)
**Hand Font:** Gochi Hand (with Comic Sans MS, cursive)

**Character:** Geist is the operating-system voice: neutral, tight and heavy in headlines, calm in lists, with stylistic set 01 and tabular figures on everywhere so counts line up. Geist Mono is what the machine typed (filenames, timestamps, page numbers, platform counts). Gochi Hand is what a person scribbled on the desk.

### Hierarchy
- **Display** (750, clamp(2.6rem, 6.4vw, 5rem), line-height 0.98, -0.035em, balanced wrap): page headlines that sit on the desk, not in a window. The home hero runs slightly larger (clamp(2.9rem, 6.3vw, 5.3rem)).
- **Headline** (700, clamp(1.4rem, 2.6vw, 1.9rem), 1.12, -0.03em): section and window headings; feature sections scale it up to around clamp(2rem, 4.2vw, 3.4rem).
- **Title** (650, 1.05rem, 1.3, -0.015em): job titles in rows (1.04rem), company names, card headings.
- **Lede** (400, 1.14rem, 1.5, `ink-2`, max 58ch): the one paragraph under a headline; its key figure is set in 650 `ink`.
- **Body** (400, 16px, 1.55, max 68ch): prose and notices. List metadata runs 13.5px.
- **Label** (600 to 650, 13 to 13.5px): field labels, group labels, menu items (500, 14px), list column heads (600, 12.5px).
- **Mono** (400, 12.5px, -0.01em, `ink-3`): window title filenames (12px), "updated 3 hours ago", page counts, posted dates, platform counts.
- **Hand** (400, 19 to 30px): sticky notes, the name tag, and short asides such as "hiring right now" or "psst, these windows move".

### Named Rules
**The Sentence Case Rule.** Headlines are sentence case and usually end with a full stop ("Every job in Dublin, sorted."). Proper nouns keep their capitals. No all-lowercase or all-caps headlines; the only capitals-only text is the printed HELLO on the name-tag sticker, because that is what a name tag says.

**The Three Voices Rule.** Geist speaks, Mono types, the hand scribbles. The hand face never carries information the visitor needs that is not also stated in Geist; mono never sets a headline.

**The One Highlight Rule.** A headline may sweep the highlighter under one phrase, once, left to right. Never more than one highlighted phrase per heading.

## Layout

A centred container (1180px max, 24px gutters, 16px under 860px) sits under a fixed 48px menu bar. Page headings sit directly on the desk (64px top, 30px bottom); the working content sits in windows below them. The home page breaks the container: the desk hero is a full-width stage (up to 1600px) with careers windows absolutely placed around a centred headline, followed by a dock, a pinned 320vh how-it-works story (two columns: steps left, a live demo window right), then the find-my-jobs window.

Spacing runs on a loose 6 / 8 / 12 / 16 / 26 / 64px rhythm: 8px between chips, 12 to 16px between row parts, 26px of window padding, 64px above page headings. Window bodies pad 26px 30px; job rows 19px 26px. Prose keeps to 68ch, ledes to 58ch or less.

**Responsive.** Breakpoints at 1180px (two desk windows and the scribble drop), 980px (desk and story reflow), 860px (single column everywhere) and 420px.
- The desk becomes a horizontal swipe strip: careers windows stand 250px wide in a scroll-snapped row at half their desktop tilt, below the headline; stickers and scribbles are hidden, and dragging is only enabled for a fine pointer.
- The menu bar keeps the wordmark and the live count and hands everything else to the burger; it drops the clock and the ".place" suffix.
- The how-it-works story is not pinned below 980px: it is an ordinary section whose steps are all readable at once while the demo window steps through on its own every four seconds when on screen.
- The dock scrolls sideways at 52px icons without tooltips; the story pins as a single column; hero primary and ghost pills fill the width.
- Job rows wrap their action column beneath the title; company rows drop the platform column into a second line; company windows become bottom sheets (12px from the edges, 72vh max).

## Elevation & Depth

Depth is literal: windows float over the desk on soft, cool, layered shadows, and height tells you what is being handled. Surfaces inside a window are flat and divided by hairlines. Glass (backdrop blur) is used only on the menu bar and the loading pill. The dock, the sticky group headers and the sticky submit bar used to be glass too; blurring what scrolls beneath them cost a repaint on every scroll frame, so they are now near-opaque fills that read the same.

### Shadow Vocabulary
- **Resting window** (`box-shadow: 0 1px 2px rgba(20,24,33,.06), 0 12px 32px -10px rgba(20,24,33,.22)`): every ordinary window and card.
- **Lifted window** (`box-shadow: 0 2px 4px rgba(20,24,33,.08), 0 30px 60px -18px rgba(20,24,33,.38)`): the window a page is about (results, find-my-jobs, company list, Get Info, story demo, careers windows on the desk), and floating pills.
- **Held** (`box-shadow: 0 3px 6px rgba(20,24,33,.1), 0 44px 70px -20px rgba(20,24,33,.45)`, plus scale 1.04): a window being dragged.
- **Popup window** (`box-shadow: 0 3px 8px rgba(20,24,33,.12), 0 50px 90px -24px rgba(20,24,33,.5)`): a company window opened over the page.
- **Small** (`box-shadow: 0 1px 2px rgba(20,24,33,.08), 0 4px 10px -4px rgba(20,24,33,.16)`): chips, small controls.
- **Aqua glow** (`0 6px 16px -6px rgba(43,116,240,.65)` under inset gloss): the primary pill only.

### Named Rules
**The Height Means Handling Rule.** Resting, lifted, held, popup: four heights, each tied to what the visitor is doing with the window. Don't invent new ones, and don't lift a window just to decorate it.

**The Glass Is For Floating Rule.** Backdrop blur only on the menu bar and transient floating chrome. Never animate `filter: blur` on entrances or loading states; windows open with opacity and transform alone.

## Shapes

Windows have gently rounded corners (14px; careers windows on the desk 12px) and clip their content. Controls and fields are 10px. Everything you press is a full pill (999px): primary, ghost, Apply, Save, field chips, status pills, the loading and submit bars. App icons are squircles (24% radius) with a white tile behind real favicons, or a pale tint of a name-derived hue carrying the initial when there is none. The dock is a 24px glass tray; the /companies platforms band opens with a 32px top edge (24px on mobile). The sticky note is the one asymmetric shape (3px 3px 18px 3px, a curled corner). Small tilts, never over about 4 degrees, give the desk a hand-placed feel: careers windows, stickers, the Get Info and story demo windows, handwritten labels.

## Components

### Buttons
Glossy and springy, like Aqua-era Mac buttons made light.
- **Shape:** full pill (999px).
- **Primary:** aqua vertical gradient, 1px `aqua-deep` edge, white 600 15px label, 11px 22px (13px 26px in the hero), an inset top highlight plus a glossy highlight across the upper 45%, and an aqua glow beneath.
- **Hover / Active:** rise 1px and scale 1.02 on the spring, 5% brighter, and any trailing arrow slides 3px. Press scales to 0.97 in 80ms. Disabled goes flat grey (#eef0f3, #6f7581 text) and loses the gloss.
- **Ghost:** the secondary way in; a white-to-#f3f4f7 pill with a `line-strong` edge and `ink` label, no gloss.
- **Apply:** leaves the site, so it is a white pill with a 1.5px aqua outline and `aqua-deep` label (13.5px, 7px 15px) that fills solid aqua on hover. It always opens the employer's posting in a new tab.
- **Save:** a small white pill with a bookmark icon; when saved it turns highlighter yellow and the icon pops in on the spring.
- **Text links:** `aqua-deep`, 600, underlined at a 3px offset; ink on hover.

### Chips
- **Field tags:** white pills (14px, 500) with a 16px dot in the group's tag colour. Hover lifts 2px on the spring. Checked, the dot becomes a solid tag-colour circle with a white tick, the chip takes an 11% tint of the tag colour, a tag-coloured border and 600 weight. Groups are headed by a 13px label with a 9px dot in the same colour.
- **Status pills:** 11.5px 650 pills, 3px 9px. Green on green wash by default; tag red for "applied" and "required"; pale yellow for "stretch".
- **Count badges:** red pills (22px high, 11.5px 700 white) in the top-right corner of app icons, ringed in white.

### Cards / Containers
- **Window:** white, 14px corners, 1px rgba(20,24,33,.12) edge, resting shadow, clipped. Title bar 38px, `win-bar` to `win-bar-2` gradient, traffic lights (12px, 7px apart), and a centred mono filename in `ink-3` (e.g. `amazon.com/careers`). Body padding 26px 30px 30px. The privacy notes window swaps the title bar for a pale legal-pad yellow.
- **Card:** a window with no title bar, for empty states and notices that don't earn one; 26px 28px padding.
- **Get Info:** a lifted, slightly tilted window with a 2x2 icon cluster and a definition list of live figures.

### Inputs / Fields
- **Style:** white, 1px `line-strong` edge, 10px corners, 10px 13px, 15px text, a faint inset top shadow. Selects carry a small up/down chevron. The company search is a pill on `win-bar` with a search icon.
- **Focus:** aqua border plus a 3.5px aqua halo at 20%. Global focus-visible is a 2.5px aqua outline offset 2px.
- **CV drop:** a 1.5px dashed grey well; aqua edge and wash on hover or drag-over (scale 1.015), solid green with green wash once a file is read.
- **Switches:** macOS switches, 38x22 grey track, white 18px knob that springs 16px across; the track turns traffic-light green when on.
- **Error:** pale red (#fdeeed) notice with `tag-red-deep` text; info notices use aqua wash and `aqua-deep`.

### Navigation
A fixed 48px glass menu bar (rgba(246,247,249,.86), blur 14px), padded for the notch with `env(safe-area-inset-*)`. Left: the two-window mark and the wordmark "sorted" in 700 with ".place" in `ink-3`; the mark tilts and grows on the spring on hover. Nav items are 14px 500 `ink-2`, 5px 10px with 7px corners; hover adds a 6% ink fill, the current page an 8% fill and 600 weight. Right: a live count with a green dot, the Dublin clock in `ink-3` (dropped under 1100px), then the account. Signed out, that is a small white **Sign in** pill carrying the Google G. Signed in, it is the person's round avatar (their Google picture, or their initial on a name-derived tint) and first name, opening a Finder-style dropdown: who they are, **Your profile**, **Saved**, **Applied**, and **Sign out** in `tag-red-deep`. Hovered items fill solid aqua, as macOS menus do. The menu bar holds still across page changes while the page beneath cross-fades quickly (0.12s out, 0.2s in; a slide read as slowness).

**Burger (860px and under).** The inline links and account give way to a 44px burger; the live count stays. It opens a sheet that drops from under the menu bar over a dimmed page: the person's avatar and email when signed in, then every destination as a 52px row with a trailing arrow (Find jobs, Companies, Your profile, Saved, Applied, Sign out). The page behind is scroll-locked, Escape or a tap on the dim closes it, and focus returns to the burger. A green dot on the burger says someone is signed in. Without JS the inline links remain and scroll sideways.

**Profile.** `/profile` replaces the separate Saved and Applied pages (both now redirect to its tabs). The avatar (76px) and "Hi, <first name>." sit on the desk with the email and how they signed in; Sign out is a ghost pill beside it. Below, a lifted window holds a segmented control (Saved | Applied with counts) that switches instantly and keeps the URL in step, and a card on the right recalls their last search with a Change search pill. Removing a row slides it out rather than reloading the page.

### Job rows
Rows in a white list inside the results window: logo (44px, 36px on mobile), employer in 13px `ink-3`, title in Title type with an aqua underline that draws in from the left on hover, 13.5px meta line, status pills, and a right-hand column with Save and Apply. Hover tints the row #f7f9fd. Sticky group headers, like Finder groups, sit under the menu bar in translucent `win-bar` with a small disclosure triangle.

### Signature: desktop objects
- **Careers windows:** small windows (30px title bars, 10px lights) showing an employer's icon, "N open in Dublin" in green, and a few real titles. On the home desk they carry a slight tilt and depth-based pointer parallax, can be picked up and thrown with momentum, and fly into the form window when the primary button is pressed.
- **Dock:** a glass tray of the biggest employers' icons (60px) with count badges and a "+N" aqua folder; icons swell up to 1.55x under the pointer and show a dark tooltip.
- **App launcher:** a grid of 64px icons with names below; hover lifts the icon, jiggles it once, and a click opens that company's jobs in a popup window that can be dragged by its title bar and closed from its red light.
- **Stickers:** a HELLO name tag with "sorted" handwritten, and a yellow sticky note carrying the live counts; both draggable on desktop.
- **Beach ball:** the loading indicator, a 26px spinning conic gradient in the traffic-light and aqua colours, inside a floating glass pill that says what is happening.

### Motion
- **Spring** (`cubic-bezier(.34,1.56,.64,1)`) for anything the visitor touches: buttons, chips, switches, icons, windows opening, the Save pop.
- **Exponential settle** (`cubic-bezier(.16,1,.3,1)`) for anything that arrives: rows filing in (34ms stagger, capped at 14 rows), windows opening on scroll, headings rising word by word (55ms per word), the highlighter sweep, page entry. The sort itself uses a fast ease-in-out (`cubic-bezier(.6,0,.25,1)`) so windows are pulled in, not bounced.
- **Counts** run up to their real value over 1.1s with a quartic ease-out.
- **Performance budget:** motion never runs where it cannot be seen. The desk's pointer parallax only listens while the desk is on screen, the scroll scatter only writes when its value changes, and the sort is 0.7s from press to form.
- **Reduced motion:** all hidden start states apply only when JS is running and `prefers-reduced-motion: no-preference`; otherwise everything is shown in place, headings are not split, counts show their final value, the beach ball stops, the dock does not magnify, parallax is off, and smooth scrolling is off.

## Do's and Don'ts

### Do:
- **Do** put working content in a window with a title bar, traffic lights and a mono filename; put page headlines on the desk itself.
- **Do** keep aqua (#2b74f0) for the next action, focus and progress, and use `aqua-deep` for any aqua text.
- **Do** colour field chips by their Finder tag (`g-*`) and nothing else by those colours.
- **Do** use real, live figures on every sticker, badge, window and headline, formatted with thousands separators, as PRODUCT.md requires.
- **Do** write headlines in sentence case with proper nouns capitalised, and at most one highlighted phrase.
- **Do** use the spring for touch and the exponential settle for arrival, and make sure every animated element is visible and usable with reduced motion or no JS.
- **Do** turn the desk into a swipe strip on narrow screens and keep the live count in the menu bar.
- **Do** show real company favicons in app icons, falling back to a lettered squircle.

### Don't:
- **Don't** switch to a dark ground or a dark "serious" register; the desktop is light.
- **Don't** add a second action colour, or use aqua, green or tag colours as decoration.
- **Don't** use the red badge or any bell, envelope or "unread" treatment to suggest alerts; the badge is a count and the site sends nothing.
- **Don't** set headlines in all lowercase or all caps, or in the mono or hand face.
- **Don't** put information only in Gochi Hand; scribbles are asides.
- **Don't** add a fifth shadow height or blur anything that doesn't float over scrolling content.
- **Don't** make desk objects draggable on touch screens, where dragging would fight scrolling.
- **Don't** add WebGL or 3D scenes; they were ruled out for phone performance.
