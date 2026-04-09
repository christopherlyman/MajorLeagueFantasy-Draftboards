# Fantasy Portfolio — UI Architecture Canonical

**Purpose:**  
This document defines the canonical truth model for the **UI architecture** of the fantasy portfolio.

It exists to help a new chat deterministically answer questions like:

- Where does UI execution begin?
- What belongs in `app.py` versus component files?
- How are public UI, commissioner tools, and debug instrumentation separated?
- Where should new page-level widgets or tabs be added?
- What are the correct structural boundaries for UI work?
- How should UI bugs be debugged without drifting into architecture mistakes?

If something conflicts:

> **DB truth → container runtime → application state → documentation**

This document is **not** a history log.

---

# 0) Scope

This canonical governs:

- Streamlit entrypoint structure
- UI execution boundaries
- component/file placement rules
- public vs commissioner UI separation
- debug instrumentation boundaries
- sidebar behavior rules
- page-level vs board-level rendering boundaries
- tab architecture
- layout-hosting rules for new UI features
- public table rendering rules
- UI verification and anti-drift rules

This canonical does **not** define:

- full draft-state restore logic
- team/franchise identity truth
- contract / PT / QO business rules except where UI projection depends on them
- auth model except where UI gating depends on it
- deployment topology

It may reference those areas only where necessary to explain UI structure.

---

# 1) Core UI Architecture Model

## 1.1 The UI Has Three Distinct Layers [VERIFIED]

The UI must be reasoned as three separate logical layers:

1. **Public DraftBoard UI**
2. **Commissioner Tools**
3. **Debug Instrumentation**

Canonical rule:

```text
Public UI ≠ Commissioner Tools ≠ Debug Instrumentation
```

These layers must not be collapsed into one surface.

## 1.2 Public UI Is League-Facing [VERIFIED]

Public UI includes surfaces intended for normal league users, such as:

- draft board
- teams tab
- available players
- other league-visible tabs

This surface must remain free of development/debug leakage.

## 1.3 Commissioner Tools Are Privileged UI [VERIFIED]

Commissioner tools are a separate privileged surface used for administrative workflows.

They are not part of the normal public product surface even when rendered on the same page.

## 1.4 Debug Instrumentation Is Not Product UI [VERIFIED]

Runtime diagnostics such as internal counters, healing flags, and debug prints are development/commissioner aids only.

Canonical rule:

```text
debug instrumentation is not product UI
```

---

# 2) Entrypoint and Root Execution Boundaries

## 2.1 Streamlit Entrypoint [VERIFIED]

The Streamlit entry file is:

```text
app/app.py
```

Its responsibilities should remain minimal.

## 2.2 Canonical Entrypoint Responsibilities [VERIFIED]

`app/app.py` should only do the following:

1. ensure `/app/src` is importable
2. configure Streamlit page settings
3. call the UI root function

Canonical structure:

```python
import streamlit as st
from draftboard.ui.app import render_app

st.set_page_config(
    page_title="Fantasy Draft Board",
    layout="wide",
)

render_app()
```

## 2.3 set_page_config Order Rule [VERIFIED]

Canonical rule:

```text
st.set_page_config() must run before any other Streamlit command
```

Violating this rule will break the app.

## 2.4 UI Root Function [VERIFIED]

The root UI execution function is:

```python
render_app()
```

Location:

```text
app/src/draftboard/ui/app.py
```

Canonical rule:

```text
all top-level UI behavior begins in render_app()
```

---

# 3) Streamlit Initialization Boundary

## 3.1 No Stateful Import-Time UI Logic [VERIFIED]

Because Streamlit re-imports modules on rerun, stateful logic must not occur at module import time.

Canonical rule:

```text
no stateful operations at import time
initialization belongs inside runtime execution paths
```

## 3.2 render_app() Is the Primary Runtime UI Boundary [VERIFIED]

Runtime-sensitive UI work should start from `render_app()` and follow proper initialization/state-loading order before rendering dependent UI.

## 3.3 UI Must Consume State, Not Invent Truth [VERIFIED]

The UI layer consumes runtime/application state and renders it.

Canonical rule:

```text
UI renders truth; UI does not invent truth
```

---

# 4) Public vs Commissioner Surface Rules

## 4.1 Public Tabs Are Hosted in ui/app.py [VERIFIED]

The main public tabs are created in:

```text
app/src/draftboard/ui/app.py
```

They are part of the public page architecture.

## 4.2 Commissioner Tools Render Separately [VERIFIED]

Commissioner tools render separately from the public tabs and remain a distinct privileged UI section.

Canonical rule:

```text
public tabs = league-visible surface
commissioner actions = privileged controls
```

## 4.3 New Commissioner Workflows Must Not Be Smuggled Into Public UI [REQUIREMENT]

Administrative controls should not be inserted casually into public league-visible surfaces unless the product requirement explicitly says the result is league-visible and the controls remain commissioner-gated.

## 4.4 League-Visible Results and Commissioner-Only Controls May Coexist [VERIFIED]

A feature may expose league-visible results while still keeping control actions commissioner-gated.

Canonical rule:

```text
league-visible display and commissioner-only control are compatible
but must remain explicitly separated
```

---

# 5) Commissioner Surface Selection and Gating Boundaries

## 5.1 Commissioner Mode Detection [VERIFIED]

Commissioner mode may be selected using the URL query parameter:

```text
?commissioner=1
```

using:

```python
st.query_params
```

Example conceptual logic:

```python
is_commissioner_url = str(st.query_params.get("commissioner", "0")) == "1"
```

## 5.2 Commissioner URL Detection and Auth Are Separate [VERIFIED]

These are different concepts:

- commissioner URL detection
- commissioner authorization
- site-admin identity
- manager auth identity

Canonical rule:

```text
URL mode detection ≠ permission/auth truth
```

## 5.3 UI Must Respect the Distinction [REQUIREMENT]

A UI surface should not assume that the commissioner URL alone is sufficient for every privileged action.

Similarly, logged-in identity should not automatically collapse commissioner-tool gating.

## 5.4 Commissioner Surface Uses the Same Public App [VERIFIED]

Commissioner access uses the same deployed public app surface, not a separate commissioner-only deployment.

Canonical rule:

```text
commissioner surface selection is a UI/routing concern
permission remains an auth concern
```

---

# 6) Sidebar and Debug Instrumentation Rules

## 6.1 Sidebar Is the Debug/Commissioner Console Surface [VERIFIED]

The Streamlit sidebar functions as a debug/commissioner console, not part of the public league-facing UI.

## 6.2 Streamlit Sidebar Behavior Constraint [VERIFIED]

Any use of:

```text
st.sidebar.*
```

causes Streamlit to create the sidebar.

Important behavior:

- sidebar exists even if empty
- users may still expand it unless explicitly suppressed

Canonical rule:

```text
removing debug text alone does not remove sidebar exposure
```

## 6.3 Public Sidebar Suppression Is Centralized [VERIFIED]

Public suppression of sidebar exposure must be controlled centrally near the start of `render_app()` using conditional CSS.

Canonical rule:

```text
sidebar suppression belongs at a centralized early control point
```

## 6.4 Debug Leakage Must Not Be Fixed by Random Print Deletion [VERIFIED/REQUIREMENT]

If debug values appear publicly, the correct investigation order is:

1. verify commissioner URL detection
2. verify sidebar suppression
3. verify no new sidebar calls bypass the gate

Canonical rule:

```text
do not solve public debug leakage by random deletion alone
```

## 6.5 Commissioner Sidebar Default State [VERIFIED]

When commissioner mode is active, the sidebar may exist but should start collapsed by default.

This is a UI ergonomics rule, not a public-product feature.

---

# 7) File and Component Placement Rules

## 7.1 app.py Is Orchestration, Not a Feature Dumping Ground [REQUIREMENT]

`ui/app.py` should remain the orchestration root and not become the permanent home for all large feature logic.

Canonical rule:

```text
app.py orchestrates
component files implement
```

## 7.2 New Page/Tab-Scale UI Should Prefer Component Extraction [VERIFIED]

For new tab-scale or feature-scale UI work, the preferred pattern is:

- wire the tab or host section in `ui/app.py`
- implement rendering in a separate component file

Example pattern:

```python
from draftboard.ui.components.some_feature import render_some_feature_tab
```

## 7.3 Separate Component Files Are Preferred for Growth [VERIFIED/REQUIREMENT]

When a feature grows large enough to have its own rendering logic, create a dedicated component file rather than enlarging `ui/app.py` unnecessarily.

Canonical rule:

```text
new file extraction = preferred growth path
broad mixed refactor during feature work = risky
```

---

# 8) Page-Level vs Board-Level UI Boundaries

## 8.1 Prove the Host Layer Before Patching UI [VERIFIED/REQUIREMENT]

Before placing a new UI feature, first prove the correct host layer.

Examples of distinct host layers:

- page-level top row
- auth host block
- pick-controls area
- public tab body
- board renderer
- commissioner-tools area

Canonical rule:

```text
do not patch first and figure out structure later
prove host layer first
```

## 8.2 Board Renderer Is Not the Default Host for Page Widgets [VERIFIED]

The DraftBoard renderer should not be assumed to be the right place for every new widget.

Board-local rendering is appropriate for board-local content.

Page-level summary widgets belong in page-level layout hosts.

## 8.3 UI Placement Must Follow Structural Ownership [VERIFIED]

Examples of correct thinking:

- page-level summary widget → page layout host
- auth width/layout fix → page-level auth host
- board header/grid behavior → board renderer
- commissioner admin workflow → commissioner tools host
- public tab display → public tabs in `ui/app.py`

---

# 9) Draft Board Renderer Boundaries

## 9.1 Board HTML Owns Board-Local Structure [VERIFIED]

Board-specific rendering belongs in the board renderer component, such as:

- grid structure
- board-local header
- board cells
- traded-pick ownership display semantics

## 9.2 Board Renderer Should Not Absorb Unrelated Page Layout Responsibilities [REQUIREMENT]

Do not place unrelated global/page widgets into board-local rendering merely because they appear visually near the board.

Canonical rule:

```text
board renderer owns board structure,
not arbitrary surrounding page furniture
```

## 9.3 Structural Fixes Beat Cosmetic Guessing [VERIFIED]

For board-local bugs, first prove:

- wrapper structure
- container boundaries
- overflow/sticky behavior
- ownership of the host layout

before applying cosmetic patches.

---

# 10) Sticky Header / Board Structure Lessons

## 10.1 Sticky Behavior Depends on Structure, Not Just CSS [VERIFIED]

A sticky header can fail even when sticky CSS exists if header and grid are not rendered in the correct shared structure.

Canonical rule:

```text
sticky CSS alone is not proof of correct sticky architecture
```

## 10.2 Shared Wrapper Rule [VERIFIED]

Board header and grid should live inside the proper shared structural wrapper when sticky local board behavior depends on them behaving as one unit.

## 10.3 Offset Tuning Is Secondary [VERIFIED]

Once structure is correct, final sticky behavior may still require offset tuning.

Canonical debugging order:

1. structure
2. container behavior
3. offset tuning

Never start with blind offset guessing.

---

# 11) Auth Host Layout Rules

## 11.1 Login/Profile Auth Block Is a Page-Level Host Layout Concern [VERIFIED]

The login/profile auth block is rendered from:

```text
app/src/draftboard/ui/app.py
```

through a local auth render helper.

It is hosted directly in page flow, not in a dedicated auth-layout component host.

Canonical rule:

```text
login/profile width and alignment are page-level host-layout concerns,
not auth-logic concerns
```

## 11.2 Root Cause of Login Stretching Must Be Proven at the Host Layer [VERIFIED]

If the login block stretches unexpectedly, prove:

- where it is hosted in page flow
- whether `.block-container` is full-width
- whether a narrowing host wrapper exists

Canonical rule:

```text
when a UI block stretches unexpectedly,
prove the host container before changing the block itself
```

## 11.3 Preferred Fix Direction for Auth Layout [VERIFIED]

Display-only login/profile layout fixes should be applied at the page host layer first, for example by narrowing the host wrapper using Streamlit columns in `ui/app.py`.

Canonical rule:

```text
display-only auth layout fixes should be applied at the page host layer first
```

## 11.4 Accepted Login/Profile Layout Goal [VERIFIED]

Preferred result:

- left-aligned auth/profile block
- constrained width rather than full-page stretch
- same host rule applies to both login and logged-in profile/password-change surfaces

---

# 12) Public Table and Tab Rendering Rules

## 12.1 Public Table UX Should Prefer Whole-Page Scrolling [VERIFIED/REQUIREMENT]

For public league-facing table displays with known row counts, the preferred UX is to expose the full content vertically and let the page scroll naturally, rather than embedding unnecessary nested scroll areas inside the table.

Canonical rule:

```text
for compact public tables with known row counts,
prefer full-page scrolling over nested internal table scrolling
```

## 12.2 Explicit Display Metadata Beats Implicit Widget Leakage [VERIFIED]

If a table needs row numbering, it should be rendered explicitly as UI data.

The implicit dataframe index must not leak into public-facing tables.

Canonical rule:

```text
explicit display metadata is acceptable;
implicit widget/index leakage is not
```

## 12.3 User-Facing Timestamps Should Be Display-Formatted [VERIFIED]

User-facing timestamps may be simplified for readability at display time.

Accepted behaviors include:

- stripping unnecessary timezone suffixes when not needed for display
- stripping fractional seconds / microseconds
- preserving canonical stored timestamp truth underneath

Canonical rule:

```text
display formatting may simplify timestamps;
storage truth must remain unchanged
```

## 12.4 Prepared HTML Tables Are Acceptable When Native Widgets Misfit the UX [VERIFIED]

When `st.table()` or `st.dataframe()` causes undesirable nested scrolling, hidden rows, index leakage, or cramped layout, it is acceptable to render the final display table as HTML from a prepared dataframe.

Canonical rule:

```text
prepared HTML table rendering is acceptable
when native Streamlit table widgets do not fit the required UX
```

This is a UI-hosting choice, not a business-logic change.

## 12.5 Teams / Lineup Display Overrides Remain Session-Layer UI [VERIFIED]

Teams-tab style lineup manipulation remains a display-only session-layer feature unless and until a persistent save path is deliberately introduced.

Canonical characteristics:

- uses session working state
- does not change DB truth
- does not persist roster truth
- only affects current browser-session display

Canonical rule:

```text
display-only lineup overrides are session state,
not persisted roster truth
```

## 12.6 Structural Duplicate Prevention Must Happen Before Auto-Fill [VERIFIED]

If a player selected in an override remains eligible for later auto-fill, the fix must occur in assignment/consumption order before render.

Canonical rule:

```text
override assignment must remove players from later auto-fill eligibility
before remaining slots are filled
```

Hiding duplicates after render is not canonical.

---

# 13) Tab Styling and Browser-Proof Rules

## 13.1 Selected-Tab Styling Should Follow the Actual Rendered Structure [VERIFIED]

If customized compact tab styling causes a moving tab highlight element to misalign visually, prefer styling the selected tab directly rather than fighting the moving highlight element.

Canonical rule:

```text
when customized tab styling causes moving-highlight misalignment,
prefer styling the selected tab directly
```

## 13.2 Browser-Level Visual Bugs Should Be Proven in DevTools [VERIFIED]

For browser-level visual misalignment, actual rendered geometry in DevTools is stronger proof than source-CSS guessing.

Canonical rule:

```text
for browser-level visual misalignment,
DevTools-rendered geometry is stronger proof than source-CSS guessing
```

## 13.3 Cosmetic Tab Styling Belongs at Centralized Page-Level CSS [VERIFIED]

Top-level tab-strip styling should be handled in centralized page-level CSS near the top-level app render path, not inside board-local rendering.

---

# 14) Working State vs Canonical Save Path

## 14.1 Session Working State Is Allowed for UI Preparation [VERIFIED]

Some UI features may prepare or stage changes in session working state before final save.

This is valid as long as:

- working state is clearly temporary
- canonical save path remains unchanged
- UI does not pretend staged state is persisted truth

## 14.2 Canonical Save Path Must Remain Clear [REQUIREMENT]

UI features must not silently create a new truth model just because the UI needs a convenient temporary representation.

Canonical rule:

```text
session working state may prepare
canonical save path persists
```

## 14.3 New Tab Features Must Not Invent Parallel Save Truth [VERIFIED/REQUIREMENT]

A new public feature should not casually invent a parallel persistence model when a canonical existing save path already exists elsewhere.

UI should bridge into canonical save paths instead of bypassing them.

---

# 15) Native Streamlit Constraints

## 15.1 Respect Streamlit Structural Constraints [VERIFIED]

The UI architecture must respect actual Streamlit constraints rather than fighting them.

Example lesson:

- expanders may not be nested

Canonical rule:

```text
Streamlit structural constraints are architecture facts,
not optional suggestions
```

## 15.2 Prefer Stable Native Patterns Where Possible [VERIFIED/REQUIREMENT]

When a fragile raw-HTML insertion lands in the wrong structural layer, prefer reverting and rebuilding using the correct native layout host rather than compounding the mistake.

---

# 16) Recovery and Safe UI Iteration Rules

## 16.1 Revert to Known-Good Baseline When a UI Patch Drifts [VERIFIED]

If a UI patch:

- lands in the wrong layer
- renders raw markup
- corrupts layout structure
- creates confusion about host ownership

the safe pattern is:

1. revert to known-good
2. identify correct host layer
3. reapply minimally

Canonical rule:

```text
when UI placement drifts badly, revert before retrying
```

## 16.2 Prefer Minimal Structural Fixes [VERIFIED/REQUIREMENT]

If the board or layout is already near-correct, prefer the smallest structural fix that restores correctness over large redesigns.

## 16.3 Do Not Mix Broad Refactors With Feature Wiring [VERIFIED/REQUIREMENT]

Feature work should not casually turn into a wide UI refactor unless that refactor is the explicit task.

Canonical rule:

```text
feature-first isolated extraction is safer than mixed large refactor
```

## 16.4 Dead UI Paths Should Be Removed After Verification [VERIFIED/REQUIREMENT]

Once the correct behavior is proven, unused dead helper code and abandoned patch paths should be removed.

Canonical rule:

```text
verified fix + dead-path cleanup = canonical end state
```

Especially in `ui/app.py`, avoid leaving:

- dead override helpers
- abandoned experimental rendering paths
- duplicate logic branches that no longer own behavior

---

# 17) Runtime Verification Procedure

## 17.1 Minimum Questions

When UI behavior looks wrong, answer these first:

1. What is the correct host layer for this UI element?
2. Is the problem public UI, commissioner tools, or debug instrumentation?
3. Is the issue structural, state-related, or purely cosmetic?
4. Is the wrong file being edited?
5. Does the UI patch belong in `ui/app.py` or a component file?
6. Is session working state being mistaken for canonical persisted truth?
7. Is a board-local problem being patched at page level, or vice versa?

## 17.2 Deterministic Debug Order [VERIFIED]

When UI behavior looks wrong:

1. DB truth if data/ownership is involved
2. imported runtime file certainty
3. runtime/app state
4. structural host layer
5. rendering/layout details

Never reverse this order.

## 17.3 Import Certainty Check [VERIFIED]

If behavior contradicts source edits, verify the imported runtime file path first.

Example pattern:

```bash
docker exec -i mlf_draftboard bash -lc 'python - << "PY"
import draftboard.ui.app as m
print(m.__file__)
PY'
```

Use equivalent checks for the specific component under investigation.

---

# 18) Verify Pack

## 18.1 Compile truth

```bash
docker exec -i mlf_draftboard bash -lc "python -m py_compile /app/app/src/draftboard/ui/app.py /app/app/src/draftboard/ui/components/board_html.py /app/app/src/draftboard/ui/components/commissioner_tools.py"
```

## 18.2 Import certainty

```bash
docker exec -i mlf_draftboard bash -lc 'python - << "PY"
import draftboard.ui.app as app_mod
import draftboard.ui.components.board_html as board_mod
import draftboard.ui.components.commissioner_tools as comm_mod
print("app:", app_mod.__file__)
print("board:", board_mod.__file__)
print("comm:", comm_mod.__file__)
PY'
```

## 18.3 Public UI verification

For the public DraftBoard surface, verify:

- no debug values visible
- no public sidebar leakage
- intended public tabs visible
- page-level widgets in intended location
- board-local UI behaves correctly
- public tables scroll naturally at page level

## 18.4 Commissioner verification

For commissioner mode, verify:

- commissioner-only controls remain gated appropriately
- sidebar is available if intended
- debug instrumentation remains confined to commissioner/debug surface
- public tabs still render correctly

## 18.5 Refresh-proof verification

After a UI patch:

- rerun app
- refresh browser
- confirm layout still behaves correctly
- confirm no unintended leakage across surfaces

---

# 19) Critical Invariants (Do Not Break)

- `app/app.py` remains a thin Streamlit entrypoint
- `render_app()` is the UI root execution boundary
- `st.set_page_config()` must run before other Streamlit commands
- no stateful import-time UI logic
- Public UI, Commissioner Tools, and Debug Instrumentation are distinct layers
- debug instrumentation must not leak into the public surface
- sidebar suppression/control belongs at a centralized early point
- `ui/app.py` orchestrates; component files implement
- new page/tab-scale UI should prefer component extraction
- prove the correct host layer before placing or patching UI
- board-local rendering belongs in board-local components
- page-level widgets do not automatically belong in the board renderer
- login/profile layout fixes belong at the page host layer first
- session working state must not be mistaken for canonical persisted truth
- new UI features must not invent parallel save truth when a canonical save path already exists
- revert to known-good baseline when a UI patch lands in the wrong structural layer
- prefer minimal structural fixes over broad mixed refactors
- selected-tab styling should follow the actual rendered structure, not guessed highlight mechanics
- prepared HTML tables are acceptable when native widgets do not meet the verified UX requirement

---

# 20) Document Intent

This document exists to help a new chat:

- reason correctly about UI structure
- place new UI work in the right layer/file
- keep public, commissioner, and debug surfaces separated
- avoid board-level vs page-level placement drift
- debug structural UI issues deterministically
- extend the UI without inventing accidental architecture
- keep display-only fixes separate from domain-truth changes

It intentionally does **not** try to document every business rule rendered by the UI.

Those details should live in companion canonicals or league overlays such as:

- Draft State / Initialization / Restore
- Team / Franchise Identity
- Pick Ownership / Pick Trades / Draft Order
- Auth / Permissions
- Deployment / Infrastructure
- league-specific overlays for MLF or MiLF differences