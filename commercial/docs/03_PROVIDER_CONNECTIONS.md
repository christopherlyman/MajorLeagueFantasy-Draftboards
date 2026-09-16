# Provider Connections

Status: DESIGN CONTRACT
Scope: Commissioner Tools commercial application
Last updated: 2026-09-15

## 1. Purpose

Commissioner Tools should retrieve league data directly from supported fantasy
platforms whenever practical.

Manual data entry remains a supported fallback for:
- Manual / Other leagues;
- unsupported providers;
- unavailable provider APIs;
- commissioners who choose not to connect an external account.

Provider integration must remain separate from league rules and Commissioner
Tools' canonical league state.

## 2. Core Ownership Model

Provider connectivity is user-owned, not league-owned.

Canonical relationship:

    auth_user
        |
        +-- provider_connection
                |
                +-- provider_league_binding
                        |
                        +-- commercial league / season

A provider connection represents authorization granted by one Commissioner
Tools user to access one external provider account.

A provider league binding connects one Commissioner Tools league/season to one
league visible through a provider connection.

## 3. Existing User Identity

`public.auth_user.user_id` is the canonical Commissioner Tools user identity.

Provider connections must reference `auth_user.user_id`.

Commissioner Tools must not create a parallel user identity system solely for
provider integrations.

Existing authentication/session infrastructure may be reused by the commercial
application when commercial authentication is implemented.

## 4. Provider Connection

A provider connection represents a user's authorization to an external fantasy
platform.

Required conceptual fields:

- provider_connection_id — durable internal identifier;
- user_id — owning `auth_user`;
- provider_code — normalized provider identifier such as `yahoo`;
- external_account_id — provider account identifier when available;
- external_account_name — provider display identity when available;
- status — connection lifecycle state;
- created_at_utc;
- updated_at_utc;
- last_verified_at_utc;
- disconnected_at_utc when applicable.

A user may have more than one provider connection.

Provider connections must not depend on a specific fantasy league.

Provider codes are application-controlled values. Initial supported commercial
provider implementation is Yahoo.

Future provider examples include:
- ESPN;
- Sleeper;
- Fantrax;
- Fleaflicker.

## 5. Provider Credentials

OAuth access tokens, refresh tokens, client secrets, authorization codes, and
similar credentials must not be stored in `league_profile`.

Provider credentials belong to the provider connection.

Credential storage must be isolated from ordinary league metadata and designed
so protected/encrypted storage can be used.

Application responses, logs, browser payloads, and normal administrative views
must never expose raw access tokens or refresh tokens.

Provider application credentials such as Yahoo client ID/client secret remain
server-side configuration and are not user-level league data.

## 6. Legacy Yahoo OAuth Data

`public.yahoo_oauth_token` is existing private-tool infrastructure.

Its current identity is based on `app_name`, including the existing
`mlf_tools` row.

That table and row remain unchanged for existing private/legacy applications.

Commercial customer authorization must not reuse the global `mlf_tools`
credential as if it represented the customer's Yahoo account.

Commercial provider authentication will use provider-connection-scoped
credentials.

## 7. Provider League Binding

A provider league binding maps one Commissioner Tools league season to one
external provider league.

Required conceptual fields:

- provider_league_binding_id;
- provider_connection_id;
- league_key — Commissioner Tools canonical league key;
- season_year;
- provider_code;
- provider_league_id — provider's canonical league identifier;
- provider_game_id — optional provider game/season identifier;
- provider_league_name — provider display name when available;
- created_at_utc;
- updated_at_utc;
- last_synced_at_utc when applicable.

For Yahoo, `provider_league_id` is the Yahoo league key, for example:

    469.l.41640

Yahoo game key may be stored separately when useful rather than inferred by
unrelated application layers.

A Commissioner Tools league/season may have at most one active binding for a
given provider.

A provider league binding does not replace the Commissioner Tools canonical
league key.

## 8. Canonical League Profile Boundary

`league_profile` remains provider-neutral.

The profile may identify the selected platform, such as:

    platform: yahoo

It does not store:
- OAuth credentials;
- refresh tokens;
- access tokens;
- provider account identity;
- Yahoo league keys;
- provider-specific authorization state.

Provider connectivity is external to the rules/profile document.

## 9. Yahoo Discovery Flow

Yahoo is the first provider-backed commercial vertical slice.

Intended customer workflow:

1. Commissioner signs into Commissioner Tools.
2. Commissioner selects or creates a Yahoo-backed league.
3. Commissioner chooses `Connect Yahoo`.
4. Commissioner authorizes their Yahoo account.
5. Commissioner Tools discovers Yahoo games/leagues accessible to that account.
6. Commissioner selects the correct Yahoo league.
7. Commissioner Tools creates the provider league binding.
8. Commissioner Tools retrieves Yahoo teams and managers.
9. Commissioner reviews an import preview.
10. Commissioner confirms initialization.
11. Commissioner Tools creates canonical durable franchises and season mappings.

No franchise rows are created merely by previewing provider data.

## 10. Yahoo Team Import Data

The existing Yahoo API integration already exposes the provider metadata needed
for franchise initialization.

For each Yahoo team, preserve when available:

- Yahoo league key;
- Yahoo team key;
- Yahoo team ID;
- Yahoo team name;
- manager / owner display name;
- manager / owner GUID.

The provider team key must remain available as the external season-team
identity.

Commissioner Tools `franchise_id` remains the durable internal franchise
identity across seasons.

Imported Yahoo teams must not be converted into fabricated internal
`commercial.<league>.t.<slot>` team keys when authoritative Yahoo team keys are
available.

## 11. Franchise Initialization

Provider-backed franchise initialization must preserve the same transaction and
validation guarantees as manual initialization.

Required invariants include:

- target commercial league/profile exists and is active;
- imported provider league belongs to the authenticated provider connection;
- imported team count equals the configured manager/team count;
- every imported team has a unique provider team key;
- franchise initialization has not already occurred for the target season;
- all franchise and season-team rows are committed atomically;
- partial initialization is not allowed.

Provider import must use the existing canonical:

- `public.franchise`;
- `public.franchise_season_team`.

No provider-specific franchise table should become the canonical ownership
model.

## 12. Manual Fallback

Manual franchise entry remains supported.

It is the primary path for:
- Manual / Other leagues;
- providers without supported integration.

For a supported connected provider, manual entry should be secondary to
provider import.

Customer-facing hierarchy for a Yahoo league should be:

    Import from Yahoo

with a secondary option such as:

    Enter teams manually

## 13. Provider Adapter Boundary

Provider-specific network/API behavior belongs behind a provider adapter or
service boundary.

Application/domain code should consume normalized provider objects rather than
Yahoo-specific response structures.

Normalized league discovery should expose concepts such as:

- provider league ID;
- league name;
- sport;
- season;
- team count.

Normalized team discovery should expose concepts such as:

- provider team key;
- provider team ID;
- team name;
- owner name;
- owner external ID.

This boundary allows future providers to implement the same application
workflow without changing canonical franchise semantics.

## 14. API Boundary

The commercial API will ultimately need provider-oriented operations such as:

- begin provider authorization;
- complete provider authorization;
- list provider connections;
- disconnect provider connection;
- discover leagues through a connection;
- bind a provider league;
- preview provider teams;
- confirm provider franchise import.

Exact route names are implementation details and are not frozen by this
document.

Authorization must verify that the authenticated user owns or is authorized to
use the referenced provider connection and commercial league.

## 15. Refresh and Rollover

Provider binding and franchise identity are separate concerns.

Season rollover preserves durable `franchise_id` identities where appropriate.

A new provider season may have new provider team keys or league identifiers.

Rollover/import logic must reconcile provider season teams to durable
franchises rather than assuming provider team keys are permanent franchise
identifiers.

Owner GUID and provider account metadata may assist reconciliation but must not
silently redefine franchise identity.

## 16. Auditability

Material provider operations should be attributable to:
- authenticated Commissioner Tools user;
- provider connection;
- commercial league/season;
- operation timestamp.

Provider imports and later synchronization should be explainable and
recoverable.

Provider data should be treated as source evidence; Commissioner Tools canonical
state remains explicit.

## 17. Initial Implementation Scope

The first implementation slice is intentionally narrow:

- Yahoo only;
- user-scoped provider connection;
- Yahoo OAuth authorization;
- Yahoo league discovery;
- Yahoo league binding;
- Yahoo team/manager preview;
- confirmed franchise initialization.

Not required in this slice:
- ESPN integration;
- Sleeper integration;
- Fantrax integration;
- Fleaflicker integration;
- automatic periodic synchronization;
- roster/player import;
- scoring import;
- standings import;
- live draft synchronization.

## 18. Database Migration Direction

Migration `003_commercial_provider_connections.sql` should implement the minimum
schema required by this contract after its exact column design is reviewed.

The migration must:
- be additive;
- preserve all existing auth and Yahoo infrastructure;
- introduce no dependency on the legacy `mlf_tools` token;
- use foreign keys to canonical Commissioner Tools identities where practical;
- be idempotent;
- be dry-run/rollback tested against the live database before application.

Credential storage design must be reviewed explicitly before real customer
OAuth tokens are persisted.

## 19. Design Decision

Provider integration is a first-class Commissioner Tools capability.

It is not encoded inside `league_profile`, and manual franchise entry is not
the default workflow for a league whose supported provider can supply the
league and team information.

Yahoo is the reference implementation for the provider adapter pattern.
