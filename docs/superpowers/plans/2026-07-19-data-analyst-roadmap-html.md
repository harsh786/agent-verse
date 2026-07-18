# Data Analyst Roadmap HTML Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polished, standalone HTML learning guide that presents the complete India data analyst roadmap with an editorial layout, accessible navigation, and print support.

**Architecture:** A single HTML document contains all semantic content, CSS, and JavaScript. The Markdown roadmap is the source of factual content; inline CSS defines responsive and print layouts, while a small progressive-enhancement script manages active navigation and printing.

**Tech Stack:** HTML5, CSS3, vanilla JavaScript, inline SVG, browser-native printing.

---

## File Structure

- Create: `docs/data-analyst-job-roadmap-india.html` - standalone reader-facing roadmap, including all styles and optional JavaScript.
- Read: `docs/data-analyst-job-roadmap-india.md` - canonical roadmap content that must be represented in the page.
- Read: `docs/superpowers/specs/2026-07-19-data-analyst-roadmap-html-design.md` - approved visual, accessibility, and validation requirements.

### Task 1: Build the Semantic Editorial Document

**Files:**
- Create: `docs/data-analyst-job-roadmap-india.html`
- Read: `docs/data-analyst-job-roadmap-india.md`
- Read: `docs/superpowers/specs/2026-07-19-data-analyst-roadmap-html-design.md`

- [ ] **Step 1: Create the document shell, landmarks, and complete content outline**

Create a valid HTML5 document with `lang="en"`, UTF-8 metadata, viewport metadata, a descriptive title, a skip link, a `header`, a labelled `nav`, a `main` element with `id="main-content"`, and a `footer`. Give every primary section a unique anchor ID:

```html
<a class="skip-link" href="#main-content">Skip to roadmap</a>
<header class="hero" id="top">...</header>
<div class="page-shell">
  <aside class="reading-rail" aria-label="Roadmap navigation">...</aside>
  <main id="main-content">
    <section id="quick-start" aria-labelledby="quick-start-title">...</section>
    <section id="mindset" aria-labelledby="mindset-title">...</section>
    <section id="excel" aria-labelledby="excel-title">...</section>
    <section id="sql" aria-labelledby="sql-title">...</section>
    <section id="bi" aria-labelledby="bi-title">...</section>
    <section id="python" aria-labelledby="python-title">...</section>
    <section id="statistics" aria-labelledby="statistics-title">...</section>
    <section id="portfolio" aria-labelledby="portfolio-title">...</section>
    <section id="hiring" aria-labelledby="hiring-title">...</section>
    <section id="readiness" aria-labelledby="readiness-title">...</section>
  </main>
</div>
```

Populate the sections from the Markdown source, including the target roles, complete tool stack, Phase 0 through Phase 7 progression, business metrics, Excel skills, SQL concepts and validation checklist, Power BI modelling and DAX snippets, Tableau scope, Python skills and pandas code, statistics/A-B testing, all five project briefs, interview preparation, India-specific applications guidance, resources, common mistakes, readiness assessment, and career directions.

- [ ] **Step 2: Use semantic content patterns for every roadmap type**

Use `table` markup with a `caption`, `thead`, and `tbody` for tool-stack, metric, and future-direction comparisons. Wrap each wide table in `.table-scroll`. Use an ordered list for the phase path, `article` elements for each project, `pre><code>` for SQL/DAX/Python samples, and an explicit competency-gate block after each skill chapter:

```html
<aside class="competency-gate" aria-labelledby="sql-gate-title">
  <p class="eyebrow">Competency gate</p>
  <h3 id="sql-gate-title">You can prove SQL fluency when...</h3>
  <p>You can solve a multi-table business problem using joins, CTEs, date logic, and a window function, then validate and explain the output.</p>
</aside>
```

Create five project `article` blocks with visible project number, skill tags, goal, questions, execution steps, deliverables, and differentiation note. Keep claims framed as analysis findings or recommendations, never invented business impact.

- [ ] **Step 3: Validate structural completeness before styling**

Run:

```bash
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('docs/data-analyst-job-roadmap-india.html', encoding='utf-8').read()); print('HTML parsed')"
```

Expected: `HTML parsed`

Run:

```bash
for anchor in quick-start mindset excel sql bi python statistics portfolio hiring readiness; do grep -q "id=\"$anchor\"" docs/data-analyst-job-roadmap-india.html || exit 1; done; printf 'All roadmap anchors found\n'
```

Expected: `All roadmap anchors found`

### Task 2: Add Responsive Editorial Styling and Print Rules

**Files:**
- Modify: `docs/data-analyst-job-roadmap-india.html`

- [ ] **Step 1: Add design tokens and readable base styles in the document head**

Create inline styles with warm paper, indigo structure, saffron action, and sage completion colours. Apply a system serif stack to headings and a system sans-serif stack to body copy. Define a high-contrast focus ring and readable line height:

```css
:root {
  --paper: #f7f3ea;
  --ink: #18211d;
  --indigo: #1e2f5d;
  --saffron: #d86320;
  --sage: #376b58;
  --line: #d6cdbc;
  --serif: Georgia, "Times New Roman", serif;
  --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
body { background: var(--paper); color: var(--ink); font-family: var(--sans); line-height: 1.65; }
h1, h2, h3 { font-family: var(--serif); line-height: 1.1; }
:focus-visible { outline: 3px solid var(--saffron); outline-offset: 4px; }
```

- [ ] **Step 2: Implement the desktop rail, editorial content rhythm, and project cards**

Use CSS grid for `.page-shell`, with a sticky 15rem rail and a content column constrained to 76rem. Style the phase path, chapter headings, competency gates, tables, inline code, code blocks, callouts, and project cards. Use borders, restrained shadows, and alternating composition rather than dense dashboard widgets. Ensure labels are present on all colour-coded states.

- [ ] **Step 3: Add mobile, reduced-motion, and print styles**

At `max-width: 980px`, collapse `.page-shell` to one column, remove sticky positioning, convert rail links to a horizontally scrollable row, and reduce heading scale. At `max-width: 640px`, reduce page padding and stack project metadata. Include:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; }
}
@media print {
  .reading-rail, .print-button, .skip-link { display: none !important; }
  body { background: #fff; color: #000; font-size: 10pt; }
  .page-shell { display: block; }
  section, article { break-inside: avoid; }
  a { color: inherit; text-decoration: none; }
}
```

- [ ] **Step 4: Check formatting and printable styles**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

Run:

```bash
grep -q '@media print' docs/data-analyst-job-roadmap-india.html && grep -q 'prefers-reduced-motion' docs/data-analyst-job-roadmap-india.html && printf 'Print and motion styles found\n'
```

Expected: `Print and motion styles found`

### Task 3: Add Progressive Enhancement and Validate the Deliverable

**Files:**
- Modify: `docs/data-analyst-job-roadmap-india.html`

- [ ] **Step 1: Add a usable static navigation and print control**

Ensure each rail link is a normal in-page anchor and add a clearly labelled button:

```html
<button class="print-button" type="button" data-print>Print or save as PDF</button>
```

This guarantees navigation works without JavaScript and gives users a direct PDF workflow.

- [ ] **Step 2: Add the optional active-link and printing script**

Add the following script just before `</body>`:

```html
<script>
  const printButton = document.querySelector('[data-print]');
  printButton?.addEventListener('click', () => window.print());

  const links = [...document.querySelectorAll('[data-nav-link]')];
  const linkById = new Map(links.map((link) => [link.getAttribute('href')?.slice(1), link]));
  const sections = [...document.querySelectorAll('main > section[id]')];

  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      links.forEach((link) => link.removeAttribute('aria-current'));
      linkById.get(visible.target.id)?.setAttribute('aria-current', 'location');
    }, { rootMargin: '-18% 0px -70% 0px', threshold: [0, 0.2, 0.5] });
    sections.forEach((section) => observer.observe(section));
  }
</script>
```

Add `data-nav-link` to each primary rail link.

- [ ] **Step 3: Validate document content and dependency-free delivery**

Run:

```bash
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('docs/data-analyst-job-roadmap-india.html', encoding='utf-8').read()); print('HTML parsed')"
```

Expected: `HTML parsed`

Run:

```bash
if grep -E 'https?://|<link[^>]+stylesheet|<script[^>]+src=' docs/data-analyst-job-roadmap-india.html; then exit 1; else printf 'No remote dependencies found\n'; fi
```

Expected: `No remote dependencies found`

Run:

```bash
git diff --check && git status --short
```

Expected: no whitespace error; status lists the new HTML document and existing unrelated workspace changes without modifying them.
