# Commissioner Tools — League Lifecycle

## Franchise Identity

Commissioner Tools uses the shared canonical franchise identity model.

### Stable franchise identity

`public.franchise.franchise_id` is the durable franchise identity across
seasons.

A franchise is not identified by a provider team key, team name, manager
name, or display label.

### Season participation

`public.franchise_season_team` records a franchise's participation in a
specific league season.

Commercial league setup writes:

- `franchise_id`: durable generated franchise identity;
- `season_year`: league profile season;
- `league_key`: canonical Commissioner Tools league key;
- `team_key`: stable season-specific internal or provider team key;
- `team_name`: commissioner-facing team name;
- `owner_name`: optional manager/owner display name;
- `source`: initialization provenance.

### Initial manual setup

When no provider team key has been imported, Commissioner Tools generates
an internal team key:

`<league_key>.t.<slot>`

Example:

`commercial.<league-id>.t.1`

The team key is season-specific identity. It is not the durable franchise
identity.

Initial commissioner-entered franchises use:

`source = 'manual'`

Provider identifiers may be attached later without replacing the durable
`franchise_id`.

### Creation invariants

For initial franchise setup:

1. the target league profile must exist;
2. the requested franchise count must equal the league profile manager count;
3. the league season must not already have franchise mappings;
4. every team name must be nonblank;
5. generated team keys must be unique;
6. franchise rows and season mappings are created in one transaction;
7. partial creation is not allowed.

### Rollover

Future season rollover preserves `franchise_id` while creating new
`franchise_season_team` mappings for the new season.

Manager identity and provider identifiers may change without changing
franchise continuity.
