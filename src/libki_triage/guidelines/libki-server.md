# AGENTS.md — libki-server

Instructions for AI agents and human contributors working in this repo. Read the ecosystem-wide [libki-coding-guidelines.md](../libki-coding-guidelines.md) first; this file adds Perl/Catalyst-specific rules.

---

## Stack snapshot

- Perl 5.40, Modern::Perl, Moose, `namespace::autoclean` everywhere.
- Catalyst 5.90 with attribute-based routing.
- DBIx::Class ORM. Raw SQL only in cronjobs where perf demands it.
- Template Toolkit views under `root/dynamic/templates/`.
- MySQL/MariaDB; schema versioning via `installer/update_db.pl`.
- Plack/Gazelle in production; `script/libki_server.pl` in dev.

---

## Contribution workflow

Fork → issue → branch → PR, per the ecosystem [libki-coding-guidelines.md §2](../libki-coding-guidelines.md#2-contribution-workflow). Upstream is `Libki/libki-server` (default branch: `master`). Open issues and PRs against upstream; push commits to your fork, never to the upstream repo.

---

## Non-negotiables

### Multi-tenancy (`instance` scoping)

Every DB query scopes by `instance`. Use `$c->instance` for the current instance:

```perl
$c->model('DB::Client')->search({ instance => $c->instance, ... });
```

Missing the instance filter exposes data from one library to another. This is the most common mistake in this codebase. Do not merge PRs that omit instance scoping on new queries.

### Location scoping

If a feature could reasonably differ between library branches, design for it from day one. Pattern: use the `Location` table and add columns or a sibling table scoped by `location_id`. Template: `Allotment` + `TimeAllowanceByLocation` setting.

Currently instance-scoped but probably should be location-scoped (partner-requested): max session length, dashboard default filter, printer-visibility rules. When adding new time/printer/dashboard settings, make them location-aware up front.

### API surface

- `/api/client/v1_0` is **legacy**. Do not extend it. Comment in [lib/Libki/Controller/API/Client/v1_0.pm](lib/Libki/Controller/API/Client/v1_0.pm) says so.
- New endpoints go under `/api/v2/` as RESTful per-action endpoints with role-based auth via [lib/Libki/Controller/API/V2/Role/Authorization.pm](lib/Libki/Controller/API/V2/Role/Authorization.pm).
- New public endpoints go under `/api/public/` with explicit auth checks.
- Existing v1 endpoints must keep working unchanged. Deployed libki-client and libki-print-manager versions across the installed base depend on them.

### Settings

Runtime config lives in the `Setting` table, scoped by `instance`. Access via `$c->setting('Name')`. Do not hardcode values an admin might reasonably want to change. Do not add `.conf` entries for things that belong in the `Setting` table.

### Auth pluggability

Auth dispatches SIP → LDAP → local in [lib/Libki/Auth.pm](lib/Libki/Auth.pm). Do not hardcode a single backend. New auth paths honor the same dispatch order, or explicitly document why they diverge.

### Timezone

All `DateTime` objects honor `$ENV{LIBKI_TZ}`:

```perl
my $now = DateTime->now( time_zone => $ENV{LIBKI_TZ} );
```

Use `$c->now()` where available. Do not call `DateTime->now` without a time_zone argument.

### Cronjobs are load-bearing

[script/cronjobs/libki.pl](script/cronjobs/libki.pl) runs every minute and decrements active session minutes. If this cron doesn't run, time enforcement silently breaks. Do not move time-decrementing logic into request handlers or optional jobs. Changes to session-time logic must be tested against a simulated missed-cron scenario.

[script/cronjobs/libki_nightly.pl](script/cronjobs/libki_nightly.pl) handles guest reset, troublemaker un-flag, and data retention. Same rules.

### Password handling

`DBIx::Class::EncodedColumn` for hashing. SIP/LDAP auth caches the patron password in the DB after a successful external auth. Do not introduce new code paths that rely on this cache — always re-delegate to the external authenticator on every request. Do not add new local-password check paths without review.

### CSRF

POST/PUT/DELETE endpoints that change state require CSRF tokens.

---

## Style

`.perltidyrc` is enforced. Run `perltidy -b` on changed files before committing.

- 100-column lines, 4-space indent, continuation indent 4.
- PascalCase packages (`Libki::Controller::Public`).
- snake_case subroutines and variables.
- UPPER_CASE constants.
- POD on modules; keep it current as signatures change.

---

## Testing

```
prove t/              # full suite
prove t/controller_API-Client-v1_0.t   # single file
```

New controllers get a test file under `t/`. Naming mirrors controller path: `lib/Libki/Controller/Foo/Bar.pm` → `t/controller_Foo-Bar.t`.

Coverage is weak overall. Bar for new code: at least one test exercising the happy path and one exercising the main failure path. See the ecosystem [TESTING.md](../TESTING.md) for required regression scenarios.

---

## Deployment

Docker-first. `docker-compose.yml` + `.env` for local. Production images built via GitHub Actions and pushed to docker.io and quay.io.

Schema migrations: add a file under `installer/` with the next version number, update the applied-version logic in `update_db.pl`. Test the migration path from the previous release, not from an empty DB.

---

## Known legacy areas, handle with care

- [lib/Libki/Controller/API/Client/v1_0.pm](lib/Libki/Controller/API/Client/v1_0.pm) — monolithic action dispatcher. Don't extend. Don't refactor speculatively either; changes here ripple to every deployed libki-client.
- `Job` table / model — appears unused; don't build on it assuming it's live.
- Mixed YAML vs `Config::General` config history — follow whichever the surrounding file already uses rather than "unifying."
