# Context Lab Frontend Design Plan

## Goal

Upgrade the existing Vue 3 + Vite workbench into a coherent, calm, high-trust interface for a student learning knowledge base. Preserve all current routes, API contracts, role permissions, persisted conversations, ingestion states, source links, and document previews.

## Design Basis

- Base system: IBM Carbon-style flat enterprise UI.
- Reading model: Mintlify-style source/document/evidence layout.
- Interaction detail: selected Linear-style compact navigation and precise state feedback.
- Default mode: light, with the existing dark mode retained and re-tokenized.
- No new UI framework unless an implementation gap makes it necessary.

## Implementation Order

1. Normalize design tokens in `frontend/src/styles/tokens.css`.
2. Rebuild the global shell in `frontend/src/styles/main.css`, including sidebar, top bar, panels, controls, focus states, and responsive breakpoints.
3. Align shared components: `AppSidebar`, `AppHeader`, `StateRail`, `SourceList`, `ModelSwitcher`, and `MemoryList`.
4. Refine `HomeView` into an actionable overview with clear counts, recent resources, task state, and entry points.
5. Refine `ChatView` into the main reading workflow: history, question composer, answer thread, evidence rail, and collapsed Agent trace.
6. Refine `IngestView` around upload, task progress, error recovery, and document list density.
7. Refine `ChunksView` into a source preview plus chunk map and metadata inspector.
8. Refine `ProfileView` and `AdminView` while preserving teacher/student distinctions.
9. Audit `LoginView` and `OnboardingView` as standalone flows.
10. Verify desktop, tablet, and mobile layouts, including offline, loading, empty, failed, and dark-theme states.

## Files Expected To Change Later

- `frontend/src/styles/tokens.css`
- `frontend/src/styles/main.css`
- `frontend/src/components/AppSidebar.vue`
- `frontend/src/components/AppHeader.vue`
- `frontend/src/components/StateRail.vue`
- `frontend/src/components/SourceList.vue`
- `frontend/src/components/ModelSwitcher.vue`
- `frontend/src/components/MemoryList.vue`
- `frontend/src/views/HomeView.vue`
- `frontend/src/views/ChatView.vue`
- `frontend/src/views/IngestView.vue`
- `frontend/src/views/ChunksView.vue`
- `frontend/src/views/ProfileView.vue`
- `frontend/src/views/AdminView.vue`
- `frontend/src/views/LoginView.vue`
- `frontend/src/views/OnboardingView.vue`

Only touch API/store/router files if a visual change exposes an existing interaction bug. Keep backend files out of the design pass.

## Acceptance Checks

- `npm run build` passes in `frontend`.
- Every existing route still resolves and role-based navigation remains correct.
- A user can upload a document, see task progress, and recover from a failed task.
- A user can ask a question, see the answer, and find its original sources.
- A teacher can inspect chunks and metadata without layout overflow.
- A student sees only the intended resources and original-file links.
- API offline, loading, empty, and error states remain explicit.
- Light and dark themes have readable contrast and the same information hierarchy.
- Verify screenshots at desktop width, tablet width, and a 390px mobile viewport.

## Working Constraint

The next design pass should be incremental and visual. Do not change the backend contract, introduce speculative product features, or replace the current icon library without a concrete need.

## Local References

- `DESIGN.md`: project-specific rules to follow during implementation.
- `DESIGN_REFERENCE_IBM.md`: local IBM reference summary and source link.
- `DESIGN_REFERENCE_MINTLIFY.md`: local Mintlify reference summary and source link.

## Upstream Source

- https://github.com/VoltAgent/awesome-design-md
- https://github.com/VoltAgent/awesome-design-md/tree/main/design-md/ibm
- https://github.com/VoltAgent/awesome-design-md/tree/main/design-md/mintlify
