# Data Analyst Roadmap HTML Design

## Purpose

Create a standalone, dependency-free HTML version of the India-focused data analyst roadmap. It should make a long learning guide easy to scan, navigate, print, and use on mobile while preserving the full substance of `docs/data-analyst-job-roadmap-india.md`.

## Audience and Success Criteria

The reader is a fresher in India preparing for an entry-level analyst role. The page succeeds when the reader can:

- understand the job-ready tool stack and learning sequence in under two minutes;
- navigate directly to a skill, project, or job-search section;
- distinguish required competency gates from optional future skills;
- use project cards as practical portfolio briefs;
- read the entire document comfortably on a phone and print it without navigation clutter.

## Deliverable

Create `docs/data-analyst-job-roadmap-india.html`.

The file must be self-contained. All CSS and JavaScript are inline; it imports no frameworks, remote fonts, images, scripts, or analytics. The Markdown source remains the canonical long-form document; the HTML is its polished reading and presentation version.

## Information Architecture

The HTML will preserve every roadmap topic using the following reader-facing structure:

1. Hero: title, outcome, audience, a concise promise, and top-level tool badges.
2. Quick-start: role target, workflow, readiness definition, and the suggested sequence.
3. Skill chapters: analytical mindset, Excel, SQL, BI, Python, and statistics.
4. Portfolio studio: five project cards with question, method, deliverables, and differentiators.
5. Hiring playbook: interview, resume, LinkedIn, applications, and practice resources.
6. Final readiness audit and next career directions.

The full desktop layout has a fixed-width reading column and a sticky left navigation rail. On screens below 980px, the rail becomes a compact top navigation and the content becomes a single readable column. Tables transform into scrollable regions rather than forcing a desktop-width layout.

## Visual Direction

Use an editorial learning-guide aesthetic rather than a generic corporate dashboard:

- Warm off-white page surface and near-black ink for durable readability.
- A saturated saffron/orange accent for action and progression, balanced by deep indigo for structural elements and muted sage for completion/check states.
- System serif headings paired with a sharp system sans-serif body; no external font dependency.
- Large, confident section numbers; thin rules; compact metadata; deliberately varied card rhythm.
- Small inline SVG or CSS-only marks may be used for decorative milestones, but no stock illustrations or icon libraries.
- `prefers-reduced-motion` must disable decorative animation. The default visual behaviour is static with minimal transition effects.

## Components

### Sticky reading rail

Contains the document title, reading purpose, anchor links to major chapters, an active-section state, and a print button. It remains visible on desktop and collapses gracefully on smaller screens.

### Hero and progress path

The hero identifies the goal as job-ready data analyst preparation. A horizontal/vertical phase path communicates Phase 0 through Phase 7 without claiming a daily schedule.

### Skill chapter

Each skill chapter has a short purpose statement, a skill standard, structured lists, example code where relevant, and a visually distinct competency gate. SQL, Power BI, and Python must retain useful code samples in accessible preformatted blocks.

### Comparison and metric tables

Tables use proper semantic `table`, `caption`, `thead`, `tbody`, `th`, and `td` elements. Wide tables live in an overflow wrapper on narrow displays.

### Project case-study card

Each project uses an identifiable number, skill tags, a decision-focused goal, key business questions, execution stages, deliverables, and a concise differentiation note. It is a portfolio brief, not merely a list of datasets.

### Hiring and readiness modules

The job-search content uses compact callouts and checklists. Checkboxes are visual only; no user data is stored. The final readiness audit uses a print-friendly checklist.

## Accessibility

- Use semantic landmarks: `header`, `nav`, `main`, `section`, `aside`, and `footer`.
- Use one `h1`; preserve sequential heading levels.
- Include a visible keyboard-focus style and a skip-to-content link.
- Give all navigation links meaningful labels.
- Ensure foreground/background combinations meet readable contrast levels.
- Do not communicate status through colour alone.
- Use a responsive base font size with comfortably spaced line height.
- Respect `prefers-reduced-motion`.

## Interaction and Error Handling

Vanilla JavaScript optionally highlights the current navigation item using `IntersectionObserver`. If JavaScript is unavailable, anchor navigation and the entire document continue to work. The print button uses `window.print()` and is omitted by print CSS. There are no network calls, user inputs, or error states.

## Verification

Validate the following after implementation:

- HTML syntax is parsable by an installed validator or parser.
- All major sections from the Markdown roadmap appear in the HTML.
- Internal navigation targets exist and work.
- The page renders at desktop and mobile widths without horizontal document overflow.
- Print CSS hides the navigation controls and preserves content clarity.
- Keyboard focus and reduced-motion styles exist.
- The document has no remote asset dependencies and `git diff --check` passes.
