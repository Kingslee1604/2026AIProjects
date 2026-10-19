# Keyboard checklist (Release 1)

The R1 bar: every task can be done with the keyboard alone, focus is always visible, and the
order of Tab stops follows the page. Automated axe checks (WCAG 2.2 A/AA, serious and critical)
run in CI on every core page in light and dark schemes (`apps/web/e2e/accessibility.spec.ts`)
and on pages with data inside each journey.

**Pass recorded 2026-10-19** on the reference laptop (Chromium 1.63 in the Playwright image,
stack at the R1-45 commit). Method: a scripted Tab walk through each page (up to 40 stops) that
recorded the focus order and checked each focused element for a visible focus indicator, plus the
keyboard-only journeys below, which run in CI. Screen-reader testing is out of scope for R1.

## Every page

| Check | Result | Evidence |
|---|---|---|
| First Tab stop is "Skip to content"; it is visible when focused; Enter moves focus to the page content | ✅ (added in R1-45: before, 8 header and navigation stops came first on every page) | `accessibility.spec.ts` "the first Tab offers to skip the navigation" |
| Header then navigation then page content; the current section is marked `aria-current="page"` | ✅ | Tab walk |
| Every focused element shows a focus indicator (outline or ring) | ✅ 0 elements without one on 7 pages | Tab walk |
| No focus lands on hidden or zero-size elements | ✅ | Tab walk |
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
| New questionnaire | "Choose file" button reaches the upload; the rest of the form after a file is chosen | ✅ upload step; ⚠ column step not walked | Tab walk |
| Run page | Steps, model calls, and retrievals; Cancel asks to confirm | ⚠ checked with the mouse only (axe passes) | `runs.spec.ts` |
| Reviews (`/app/reviews`) | Each run waiting for review is a link | ✅ | Tab walk |
| Review a run | **A** approve, **E** edit (Ctrl+Enter saves), **R** reject (reason, Enter), **J/K** next/previous; focus moves to "Complete review" when all are decided; Enter completes | ✅ | `reviews.spec.ts` "decides every answer with the keyboard" |
| Admin (`/app/admin`) | Name, scope checkboxes, expiry select, Create key; Copy and Done; Revoke asks to confirm | ✅ | Tab walk; `api-keys.spec.ts` |

## Not yet checked by a person

These need a human and are open for the next pass:

- The ⚠ rows above: the questionnaire column step and the run page with the keyboard only.
- Zoom to 200% and 400% (reflow) on each page.
- Windows High Contrast mode (forced colours).
- A screen reader (NVDA on Windows) on Ask and Review.
