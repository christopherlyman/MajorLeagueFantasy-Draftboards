# Fantasy Portfolio — Thin Core Canonical Guide

**Purpose:**  
This document teaches a new chat how to **deterministically discover system truth** and safely continue development across the fantasy portfolio without drift.

It is **not** a history log, feature ledger, or full architecture spec.

If something conflicts:

> **DB truth → container runtime → application state → documentation**

**Operating principle:** Teach how to verify, not what to assume.

---

# 0) Anti-Drift Contract

## 0.1 Fact Status Types

All claims should be labeled when documenting new facts:

- **[VERIFIED]** — confirmed via DB query or container execution
- **[OBSERVED]** — seen once; re-verify before relying
- **[PENDING]** — suspected; not canonical
- **[REQUIREMENT]** — business rule intent

## 0.2 How a New Chat Should Start

When uncertain:

1. Query database truth
2. Verify container runtime behavior
3. Inspect application state
4. Use documentation only as guidance

Never reverse this order.

---

# 1) Runtime Authority Rules

## 1.1 Authority Order

```text
Database > Container Runtime > Application State > Documentation
```

Documentation never overrides live system truth.

## 1.2 Execution Environment Rule

Host NAS Python is **non-authoritative**.

Always execute Python inside the relevant live container.

Example pattern:

```bash
docker exec -i mlf_draftboard bash -lc "python -V"
```

Use the correct live container name for the league/runtime under investigation.

## 1.3 Import Certainty Rule

If behavior contradicts source edits, verify the file actually imported by runtime.

Example pattern:

```bash
docker exec -i mlf_draftboard bash -lc 'python - << "PY"
import draftboard.ui.app as m
print(m.__file__)
PY'
```

Use this pattern for any module under investigation.

## 1.4 Fix Workflow (Deterministic Order)

Always debug in this order:

1. DB rows / views
2. Loader output in container runtime
3. Session / application state
4. UI rendering

Never start with UI fixes.

## 1.5 Streamlit Initialization Law

Streamlit reruns re-import modules.

Therefore:

- no stateful operations at import time
- initialization belongs inside runtime execution paths such as `render_app()`

---

# 2) Portfolio / League Boundary Rules

## 2.1 Portfolio Root vs League Root

Going forward, distinguish between:

- **portfolio root** — the parent area that contains shared assets and league roots
- **league root** — the root folder for one specific deployed league/runtime

Canonical rule:

```text
do not confuse portfolio structure with current live league-local deployment ownership
```

## 2.2 Shared Canonicals vs League Overlays

Shared canonicals own:

- anti-drift rules
- shared subsystem truth
- shared architecture direction
- shared deployment/infrastructure patterns

League overlays own:

- league-only rule differences
- league-only runtime/env differences
- league-only workflows
- league-only future exceptions to shared canonicals

Canonical rule:

```text
shared canonicals define common truth
league overlays define only real deviations
```

## 2.3 Do Not Solve Drift by Duplicating Canonicals

Do not maintain three divergent full canonical sets for Shared, MLF, and MiLF.

Canonical rule:

```text
prefer one shared canonical plus thin league overlays
over duplicated full canonical sets
```

---

# 3) Application Entrypoint Rules

## 3.1 Streamlit Entrypoint

The app entrypoint should remain a thin launcher.

Typical current pattern:

```text
app/app.py
```

## 3.2 UI Root

Top-level UI behavior should begin in the root UI module/function.

Typical current pattern:

```text
app/src/draftboard/ui/app.py
render_app()
```

## 3.3 Entrypoint Responsibility Rule

Entrypoint files should remain minimal. They should:

1. ensure imports work
2. configure page settings
3. call the root UI function

Canonical rule:

```text
do not hide business logic or state mutation inside thin entrypoint wrappers
```

---

# 4) Data Authority Model

## 4.1 Player Identity Rule

Players are uniquely identified by:

```text
yahoo_player_key
```

Names are never authoritative.

## 4.2 Scope Rule

League data must be scoped by:

```text
(league_key, season_year)
```

Unscoped queries are invalid.

## 4.3 Canonical Domains

Use the database as source of truth for system domains. In particular:

- player identity/universe comes from DB, not ad hoc UI state
- team/franchise mappings come from DB SSOT tables
- draft state is persisted and must be proven from authoritative persisted state, not UI appearance
- auth identity/role mapping comes from canonical auth tables, not UI assumptions
- deployment truth comes from live runtime configuration, not old scripts or memory

## 4.4 Derived Data Rule

- tables store facts
- views express contracts
- loaders translate DB → runtime state
- UI consumes runtime state only

UI is never authoritative.

---

# 5) Draft State Rules

## 5.1 Persisted State Matters

Draft state must be proven from persisted canonical state, not from what the UI appears to show in one session.

## 5.2 Restore Precedence

When diagnosing state restore/reset behavior, prove the real restore path before assuming disk reset is enough.

## 5.3 Draft Order Rule

Draft order must be reasoned from its canonical derivation path, not inferred from visual placeholders.

## 5.4 Ownership Rule

Column identity, pick ownership, keeper placeholders, and QO placeholders are different concepts and must not be conflated.

---

# 6) Identity Rules

## 6.1 Season Team Identity

Current-season team identity is season-scoped and must come from the canonical season mapping tables.

Do not treat UI order, slot number, or legacy placeholders as team identity.

## 6.2 Cross-Season Identity

Cross-season continuity belongs to franchise-level identity, not raw season team keys.

When reasoning across seasons, distinguish:

- stable franchise identity
- season-specific team mapping
- display labels
- owner linkage helpers

---

# 7) Normalization Boundary Rule

Legacy keyspaces or legacy representations may exist in stored data.

Canonical rule:

```text
normalization happens at one boundary
not scattered across rendering or ad hoc UI helpers
```

If UI data looks wrong:

```text
STOP → verify the canonical initialization / normalization boundary
```

Do not patch symptoms in rendering first.

---

# 8) Auth / Permissions Routing Rules

## 8.1 Local Auth Direction

The app uses a canonical local auth model for league users.

Do not invent parallel auth systems unless deliberately redesigning auth.

## 8.2 Identity Mapping Rule

Human/manager permissions should be reasoned through canonical user → league role → franchise/team mapping, not by UI assumptions.

## 8.3 Permission Rule

Authenticated write access must be enforced at the actual authoritative action path, not only by display logic.

## 8.4 Separate Commissioner Gate

Commissioner-tool access and manager identity are separate concepts and must not be conflated.

---

# 9) Deployment Routing Rules

## 9.1 Deployment Truth Lives in the Deployment Canonical

Current deployment SSOT, current env SSOT, container topology, proxy routing, and auth-bridge runtime facts belong in the deployment/infrastructure canonical.

This core guide should route there rather than duplicate those details.

## 9.2 DB Connectivity Rule

If app-to-DB connectivity is in question, prove the live DSN host and live network path from inside the running container.

## 9.3 Public Access Rule

League-facing access should be reasoned through the reverse proxy / HTTPS path, not raw internal service assumptions.

## 9.4 Restart-Window Caution

Transient restart-window proxy errors are not by themselves proof of broken steady-state deployment.

Canonical rule:

```text
prove steady-state runtime truth after warm-up,
not only transient restart-window noise
```

---

# 10) Deterministic Debug Procedure

When something looks wrong, verify in this order:

1. Is the DB truth correct?
2. Is the container running the code/file you think it is?
3. Does the loader return the expected runtime data?
4. Is state/session correct after initialization?
5. Only then inspect rendering/UI behavior

Do not skip steps.

---

# 11) Verify Pack (Run Before Any Fix)

## 11.1 Container sanity

Example pattern:

```bash
docker exec -i mlf_postgres psql -U mlf -d mlf -c "select 1;"
docker exec -i mlf_draftboard bash -lc "python -V"
```

Adjust names to the currently deployed league/runtime when needed.

## 11.2 Import certainty

Example pattern:

```bash
docker exec -i mlf_draftboard bash -lc 'python - << "PY"
import draftboard.ui.app as m
print(m.__file__)
PY'
```

## 11.3 Compile check

Example pattern:

```bash
docker exec -i mlf_draftboard bash -lc "python -m py_compile /app/app/src/draftboard/ui/app.py"
```

## 11.4 Scope discipline

Before changing any league behavior, prove the exact:

- `league_key`
- `season_year`
- canonical table/view/function involved

---

# 12) Critical Invariants (Do Not Break)

- Player identity always = `yahoo_player_key`
- League data must be scoped by `(league_key, season_year)`
- DB truth outranks runtime assumptions
- UI is never authoritative
- Normalization belongs at a single boundary, not in scattered UI patches
- Streamlit import-time side effects are unsafe
- Debug in order: DB → loader → state → UI
- Verify imported file path before assuming a code edit is live
- Persisted state must be proven before assuming a reset worked
- Commissioner access and manager auth are separate concepts unless deliberately redesigned
- Shared canonicals should not be duplicated into drifting league-local copies

---

# 13) Document Intent

This document exists to help a new chat:

- locate authoritative truth
- verify runtime behavior
- reason deterministically
- avoid drift
- avoid patching symptoms before proving causes
- choose the correct companion canonical
- distinguish shared truth from league-specific overlays

It intentionally excludes:

- detailed feature history
- old debugging narratives
- temporary addendum sediment
- subsystem detail that belongs in companion canonicals

---

# 14) Canonical Documentation Map

This thin core is the **root operating manual**.

Use it first to determine:

- how to verify truth
- what order to debug in
- what not to trust
- which deeper canonical to open next

When the issue is domain-specific, move from this core guide into the correct companion canonical below.

## 14.1 How to Use the Canonicals

Use this guide in the following order:

1. Start with **0_CoreCanonicalGuide.md**
2. Identify the domain that matches the problem
3. Open the matching domain canonical
4. Follow that canonical’s:

   - scope
   - truth model
   - invariants
   - verification procedure
   - verify pack

5. If multiple domains are involved, use the domain that owns the **authoritative truth** first, then follow dependencies outward

Canonical rule:

```text
root guide teaches how to verify
domain canonicals teach what is authoritative within that subsystem
```

## 14.2 Shared Companion Canonicals

### 1) Draft State / Initialization / Restore

Use this canonical when the issue involves:

- autosave or persisted state
- restore precedence
- state healing
- initialization behavior
- placeholder rebuilds
- reset behavior
- state persistence after rerun/refresh
- differences between visible board state and persisted truth

This canonical owns:

- persisted DraftBoard state reasoning
- initialization and healing rules
- restore precedence
- placeholder rebuild expectations

### 2) Team / Franchise Identity

Use this canonical when the issue involves:

- `franchise_id`
- current-season `team_key`
- season rollover
- team mapping
- cross-season continuity
- `owner_guid`
- identity confusion between display labels and canonical identity

This canonical owns:

- franchise continuity
- season team assignment SSOT
- canonical current-season team identity
- rollover mapping model

### 4) Pick Ownership / Pick Trades / Draft Order

Use this canonical when the issue involves:

- slot identity
- current pick ownership
- traded picks
- draft order
- `owner_team_key`
- `original_team_key`
- QO-round traded pick behavior
- pick-trade persistence

This canonical owns:

- draft-order baseline truth
- current pick ownership truth
- traded-pick semantics
- persistence of pick ownership changes

### 5) Auth / Permissions

Use this canonical when the issue involves:

- login behavior
- local auth
- manager identity
- site admin vs commissioner unlock
- password reset
- password change
- persistent login cookies
- session revocation
- login rate limiting
- pick-submit permission checks

This canonical owns:

- canonical auth identity/role/session surfaces
- authoritative permission rules
- local auth persistence truth

### 6) UI Architecture

Use this canonical when the issue involves:

- `app.py` vs component placement
- public tabs vs commissioner tools
- sidebar/debug leakage
- host layout selection
- page-level vs board-level widget placement
- structural UI bugs
- component extraction boundaries

This canonical owns:

- UI layering
- root execution boundaries
- component/file placement rules
- public vs commissioner vs debug separation

### 7) Deployment / Infrastructure

Use this canonical when the issue involves:

- Docker deployment
- compose truth
- networking
- app-to-DB connectivity
- Caddy/reverse proxy
- HTTPS/public hostname
- auth-bridge routing/runtime
- bind mounts
- rebuild vs rerun behavior
- runtime env/dependency truth

This canonical owns:

- current deployment SSOT
- current env SSOT
- runtime container topology
- reverse proxy / TLS architecture
- image/build/runtime dependency rules
- auth-bridge infrastructure/routing truth

### 8) Multi-League Target Architecture

Use this canonical when the issue involves:

- portfolio root vs league root
- shared-vs-league boundaries
- future multi-league structure
- self-contained league-instance direction
- template/profile-driven future architecture

This canonical owns:

- long-term multi-league target structure
- boundary rules for shared vs league-specific assets
- future-state architecture direction

## 14.3 League Overlay Canonicals

### MLF Overlay

Use the MLF overlay when the issue involves **MLF-only** differences such as:

- contracts
- prospect tags
- qualifying offers
- MLF-only commissioner workflows
- MLF-only next-season prep differences

### MiLF Overlay

Use the MiLF overlay when the issue involves **MiLF-only** differences such as:

- redraft-only behavior
- MiLF-only runtime/env differences
- MiLF-only next-season prep differences

Canonical rule:

```text
shared canonicals own common truth
league overlays own only real deviations
```

## 14.4 Cross-Domain Usage Rule

Some issues span multiple canonicals. Use the owning domain first.

Examples:

- Board looks wrong after refresh:

  - start with **Draft State / Initialization / Restore**
  - then use **Pick Ownership / Pick Trades / Draft Order** if ownership/trade semantics are involved
  - then use the league overlay if the issue is league-specific

- Manager cannot draft:

  - start with **Auth / Permissions**
  - then use **Team / Franchise Identity** if mapping identity is suspect
  - then use **Pick Ownership / Pick Trades / Draft Order** if on-clock ownership appears wrong

- Public UI shows wrong ownership/debug behavior:

  - start with the owning truth domain first
  - use **UI Architecture** only after state/ownership/auth truth has been proven

Canonical rule:

```text
do not start with UI Architecture
when the real question is state truth, identity truth, auth truth, or deployment truth
```

## 14.5 Canonical Ownership Rule

When a topic belongs clearly to one canonical, that canonical should be treated as the subsystem owner.

Use this core guide to route the investigation.

Do not duplicate full subsystem truth back into the core guide.

---

# 15) Companion Docs Recommended

This thin core should be paired with these shared canonicals:

- `1_DraftState_Initialization_Restore.md`
- `2_Team_Franchise_Identity.md`
- `4_Pick-Ownership_Pick-Trades_Draft-Order.md`
- `5_Auth_Permissions.md`
- `6_UI_Architecture.md`
- `7_Deployment_Infrastructure.md`
- `8_Multi-League_Target_Architecture.md`

And with these league overlays as applicable:

- `mlf/docs/9_MLF_League_Overlay.md`
- `milf/docs/9_MiLF_League_Overlay.md`

Addendums should be temporary and later consolidated into the correct subsystem or overlay canonical.