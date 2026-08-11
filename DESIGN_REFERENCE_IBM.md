# IBM Carbon Reference Summary

Source: https://github.com/VoltAgent/awesome-design-md/tree/main/design-md/ibm

This is the local implementation summary of the IBM entry from `awesome-design-md`. The upstream file is linked above; the project-specific rules in the root `DESIGN.md` take precedence.

## Key Direction

- Enterprise-clean, flat, technical, and highly structured.
- White canvas with light gray surfaces and thin borders.
- IBM Blue `#0f62fe` is the primary accent.
- Charcoal text `#161616` with muted gray secondary text.
- Corners stay square or use very small radii, usually 0px to 4px.
- Cards use borders and surface changes instead of heavy shadows.
- IBM Plex Sans is the main typeface; use IBM Plex Mono for technical values.

## Useful For Context Lab

- Sidebar and application shell.
- Upload and ingestion task lists.
- Chunk tables and metadata inspectors.
- Admin views and role-specific operations.
- Status, validation, error, and offline states.

## Adaptations Required

- Use `Noto Sans SC` as the Chinese fallback.
- Keep a light reading-first default for study content.
- Retain 6px to 8px radii only where the current UI needs them for compact controls.
- Use semantic green, yellow, and red only for state communication.
