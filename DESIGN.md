# Context Lab Frontend Design System

## Design Direction

Use IBM Carbon as the primary visual language, with Mintlify-inspired documentation reading patterns. This is a working knowledge system for students and teachers, not a marketing site. Optimize for frequent operations, long-form reading, source traceability, and clear processing states.

The interface should feel precise, calm, trustworthy, and technical. Keep the light theme as the default because users read study material for long periods. Provide a dark theme that preserves the same hierarchy.

## Product Surface

The main workflows are:

1. Sign in and onboarding.
2. Inspect the knowledge-base overview.
3. Upload and monitor document ingestion.
4. Ask grounded questions against all or selected sources.
5. Inspect retrieved sources and original documents.
6. Review chunks, metadata, OCR confidence, and page locations.
7. Manage student profiles and class resources as an admin.

## Visual Tokens

### Color Roles

- `--canvas`: `#ffffff`
- `--surface-1`: `#f4f4f4`
- `--surface-2`: `#e0e0e0`
- `--ink`: `#161616`
- `--ink-muted`: `#525252`
- `--ink-subtle`: `#8c8c8c`
- `--primary`: `#0f62fe`
- `--primary-hover`: `#0050e6`
- `--primary-pressed`: `#002d9c`
- `--success`: `#24a148`
- `--warning`: `#f1c21b`
- `--danger`: `#da1e28`
- `--hairline`: `#e0e0e0`
- `--inverse-canvas`: `#161616`
- `--inverse-surface`: `#262626`

Use blue for primary actions, active navigation, focus, links, and selected chunks. Use semantic colors only for status. Do not use gradients, decorative blobs, or multiple competing accent colors.

### Typography

- UI font: `IBM Plex Sans`, then `Noto Sans SC`, then system sans-serif.
- Code and metadata: `IBM Plex Mono`, then `ui-monospace`, monospace.
- Body: 14px to 16px, line-height 1.5.
- Compact labels: 12px to 13px, line-height 1.4.
- Page heading: 32px maximum in the workbench.
- Panel heading: 20px to 24px.
- Avoid oversized marketing display type and avoid negative letter spacing in Chinese text.

### Shape, Spacing, and Depth

- Prefer 0px to 4px corner radius for workbench surfaces.
- Use 8px as the base spacing unit: 8, 16, 24, 32, 48.
- Prefer borders and surface changes over shadows.
- Keep one clear outer panel hierarchy; do not nest decorative cards inside cards.
- Interactive targets must be at least 36px high and icon buttons must have visible tooltips.

## Layout Rules

### Application Shell

- Desktop: fixed 232px to 248px sidebar plus a flexible content area.
- Sidebar: product identity, primary navigation, role-specific navigation, and a compact service state.
- Top bar: current page title, API state, identity, theme switch, and sign out.
- Main content: max-width 1440px with 24px to 32px horizontal padding.
- Keep navigation labels short and put secondary descriptions in muted text.

### Knowledge Q&A

Use a three-zone reading layout when the viewport permits:

- Left: conversation history and scope controls.
- Center: question composer and answer thread.
- Right: sources, evidence sufficiency, page references, and expandable Agent trace.

On narrower screens, collapse the history and evidence zones into drawers or disclosure panels. The answer and source links remain the primary content.

### Ingestion and Chunk Inspection

- Use a task list with stable rows, status color, progress, file type, and timestamps.
- Keep upload controls above the task list and make processing errors actionable.
- For chunk inspection, use a document/source selector, original preview, chunk map, and inspector.
- Preserve original page order and make the selected chunk visually unambiguous.
- Show OCR confidence, page range, content type, and metadata as labeled facts, not as prose.

## Component Rules

### Buttons

- Primary: solid IBM blue, white text, square or 2px radius.
- Secondary: white or surface background with a border.
- Tertiary: text or icon action with no container.
- Danger: reserved for destructive actions and requires confirmation where needed.
- Use familiar icons inside tool buttons. Text is allowed for clear commands such as Upload, Ask, Open original, and Delete.

### Panels and Tables

- Use flat white panels on a light gray canvas.
- Use 1px hairlines, not large shadows.
- Panel headers have a title, a short context label, and one main action at most.
- Repeated documents, tasks, sources, and students should align as rows or dense list items.
- Status should be understandable from text and color together.

### Chat Answers and Evidence

- Keep the answer copy readable with a comfortable measure of 680px to 760px.
- Render sources as compact linked rows with filename, page, content type, and relevance.
- Make evidence status prominent but quiet: `Evidence sufficient`, `Evidence weak`, or `Expanded search`.
- Keep Agent trace collapsed by default and expose tool calls only when useful for debugging or review.

### Forms and Empty States

- Labels sit above controls and remain visible after input.
- Validation appears next to the affected control with an actionable message.
- Empty states should explain the next action in one sentence and use one restrained icon.
- Loading states preserve layout dimensions with skeleton rows or a progress rail.

## Responsive Behavior

- At 1100px: reduce sidebar width and move secondary evidence content below the answer.
- At 820px: collapse the sidebar to an icon rail or drawer, with an explicit menu button.
- At 640px: use one-column layouts, full-width controls, and horizontally scrollable compact toolbars.
- Do not let filenames, source links, status labels, or buttons overflow their containers.
- Preserve a minimum 44px touch target on mobile.

## Motion and Accessibility

- Use short 150ms to 200ms transitions for hover, focus, and drawer changes.
- Do not animate document content or make progress states distracting.
- Preserve visible keyboard focus, semantic labels, and readable contrast.
- Respect `prefers-reduced-motion`.
- Theme changes must not change layout or text meaning.

## Do and Do Not

### Do

- Make source provenance visible close to every generated answer.
- Use consistent page geometry across Home, Ingest, Chat, Chunks, Profile, and Admin.
- Treat the original document and chunk inspector as first-class reading tools.
- Use mono typography for technical metadata only.
- Keep the UI useful when the API is offline or an ingestion task fails.

### Do Not

- Do not turn the workbench into a landing page.
- Do not use dark purple gradients, floating blobs, glassmorphism, or oversized hero sections.
- Do not hide important retrieval controls behind unexplained icons.
- Do not use color as the only signal for status.
- Do not replace real source evidence with decorative AI illustrations.

## Reference Sources

- Primary reference: IBM Carbon analysis from `VoltAgent/awesome-design-md`.
- Secondary reference: Mintlify documentation layout analysis from `VoltAgent/awesome-design-md`.
- The project-specific rules above take precedence over the raw reference documents.
