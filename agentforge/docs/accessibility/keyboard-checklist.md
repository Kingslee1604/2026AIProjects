# Keyboard checklist (Release 1)

The R1 bar: every task can be done with the keyboard alone, focus is always visible, and the
order of Tab stops follows the page. Automated axe checks (WCAG 2.2 A/AA, serious and critical)
run in CI on every core page in light and dark schemes (`apps/web/e2e/accessibility.spec.ts`)
and on pages with data inside each journey.

**Pass recorded 2026-10-19** on the reference laptop (Chromium 1.63 in the Playwright image,
stack at the R1-45 commit). Method: a scripted Tab walk through each page (up to 40 stops) that
recorded the focus order and checked each focused element for a visible focus indicator, plus the
keyboard-only journeys below, which run in CI. Screen-reader testing is out of scope for R1.

**Manual pass by the owner, 2026-10-21**, on the redesigned UI in Edge/Chrome on Windows 11:
keyboard only (new questionnaire including the column step, the run page), zoom to 200% and 400%,
and Windows High Contrast. All passed. Two problems were found during the pass and fixed before
recording it:

- **The focus ring was hard to see on the dark theme.** It was a 2px outline that started as the
  text colour and faded to the accent. It is now a 3px accent ring with a 3px gap, shown
  immediately, on every element.
- **An invisible Tab stop.** The hidden file input behind "Choose file" (new questionnaire) and
  "Choose files" (knowledge base) took focus after the button. The scripted walk missed it. It is
  now out of the Tab order (`tabIndex=-1`, hidden from assistive technology), and a test covers
  it.

## Every page

| Check | Result | Evidence |
|---|---|---|
| First Tab stop is "Skip to content"; it is visible when focused; Enter moves focus to the page content | ✅ (added in R1-45: before, 8 header and navigation stops came first on every page) | `accessibility.spec.ts` "the first Tab offers to skip the navigation" |
| Header then navigation then page content; the current section is marked `aria-current="page"` | ✅ | Tab walk |
| Every focused element shows a focus indicator (outline or ring) | ✅ 3px accent ring everywhere (strengthened 2026-10-21) | Tab walk; manual pass |
| No focus lands on hidden or zero-size elements | ✅ (fixed 2026-10-21: the hidden file inputs were Tab stops) | `accessibility.spec.ts` "file pickers have one Tab stop"; manual pass |
| No keyboard trap: Tab always moves on and returns to the browser | ✅ | Tab walk |

## Pages and tasks

| Page | Task done with the keyboard only | Result | Evidence |
|---|---|---|---|
| Sign in (`/login`) | The first Tab reaches Email; Tab to Password; Enter signs in | ✅ | Tab walk; `auth.spec.ts` |
| Overview (`/app`) | Links to Ask and Questionnaires; Sign out everywhere | ✅ | Tab walk |
| Knowledge (`/app/knowledge`) | Each knowledge base is a link; Name field and Create follow the list | ✅ | Tab walk; `knowledge.spec.ts` |
| Knowledge base | Upload via the file button (Enter/Space opens the picker); search box; Enter searches | ✅ | `knowledge.spec.ts` |
| Ask (`/app/ask`) | Knowledge-base checkboxes (Space toggles), question box, **Ctrl+Enter** asks; Tab to a sentence and **Enter** opens its cited passage | ✅ | `ask.spec.ts` "keyboard only" |
| Questionnaires (`/app/runs`) | New questionnaire; status filters are buttons; each run is a link | ✅ | Tab walk; `runs.spec.ts` |
| New questionnaire | "Choose file" opens the picker with Enter; column dropdowns change with the arrow keys; confirm; knowledge base ticked with Space; Start reached | ✅ | Manual pass 2026-10-21 |
| Run page | Review answers and Cancel; Enter on a question or a step chip opens its details; the panel's tabs; the Events list scrolls with the arrow keys | ✅ | Manual pass 2026-10-21; `runs.spec.ts` |
| Reviews (`/app/reviews`) | Each run waiting for review is a link | ✅ | Tab walk |
| Review a run | **A** approve, **E** edit (Ctrl+Enter saves), **R** reject (reason, Enter), **J/K** next/previous; focus moves to "Complete review" when all are decided; Enter completes | ✅ | `reviews.spec.ts` "decides every answer with the keyboard" |
| Admin (`/app/admin`) | Name, scope checkboxes, expiry select, Create key; Copy and Done; Revoke asks to confirm | ✅ | Tab walk; `api-keys.spec.ts` |

## Zoom and contrast (manual pass 2026-10-21)

| Check | Result |
|---|---|
| Zoom 200%: Overview, Ask (with an answer), a run page, Admin: readable, no overlap or clipping, no sideways scrolling | ✅ |
| Zoom 400%: the same pages; the sidebar becomes a top bar | ✅ |
| Windows High Contrast (forced colours): sign-in, Overview, Ask, a run page: text, button and field edges, badges, and the focus ring visible | ✅ |

## Not yet checked

- A screen reader (NVDA on Windows) on Ask and Review: optional for R1, planned before a public
  launch.
