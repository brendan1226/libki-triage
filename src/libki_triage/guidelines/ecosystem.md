# Libki Coding Guidelines (Ecosystem-Wide)

These are the rules that apply across every Libki repo. Per-repo `AGENTS.md` files add stack-specific detail. Every rule here is non-negotiable unless explicitly marked aspirational.

## Audience

Written for AI coding agents and human contributors working in any of: libki-server, libki-client, libki-print-station, libki-print-manager.

---

## 1. Fix discipline

Every bug-fix PR must include:

1. **Reproducing scenario.** Steps or a test that demonstrates the bug. "User reported X" is not a reproduction.
2. **Root-cause statement.** One or two sentences explaining *why* the bug happened, not just what triggers it.
3. **Regression test.** A test that fails before the fix and passes after. If writing one is not feasible, the PR must state the reason explicitly.

"Try this and see if it works in production" is not acceptable. A fix without a reproduction is a guess.

---

## 2. Contribution workflow

Fork → issue → branch → PR. All Libki contributions go through a personal or organizational fork on GitHub. Even contributors with write access to the upstream `Libki/*` repos use the fork flow so every change takes the same review path.

**ByWater contributors fork to the `bywatersolutions/` org.** External contributors fork to their own account.

### One-time setup (per repo)

```bash
gh repo fork Libki/libki-server --clone
cd libki-server
git remote -v   # origin = your fork, upstream = Libki/libki-server
```

If the repo is already cloned from upstream, fork without cloning and fix the remotes:

```bash
gh repo fork Libki/libki-server
git remote rename origin upstream
git remote add origin git@github.com:<your-account-or-org>/libki-server.git
```

Note: `gh repo fork <repo>` with a repo argument creates the fork on GitHub but does **not** clone or add a remote — so the manual `git remote` steps above handle the local wiring. The `--clone` and `--remote` flags of `gh repo fork` are only valid when invoked without a repo argument (from inside an already-cloned upstream directory).

### Per-change flow

Default branches vary per repo. Most Libki repos use `master`; `libki-print-station` uses `main`. Examples below use `master` — substitute `main` where applicable. Each repo's `AGENTS.md` states the actual name.

1. **Sync your fork with upstream.**

    ```bash
    git fetch upstream
    git switch master && git merge --ff-only upstream/master
    ```

2. **Open an issue on the upstream repo.** Use the repo's issue template if one exists. Capture reproduction steps, expected vs actual behavior, and environment (app version, OS, Libki server version, relevant config).

    ```bash
    gh issue create --repo Libki/libki-server --title "..." --body-file ./issue-body.md
    ```

3. **Branch off `upstream/master`.** Naming: `issue-<N>-short-description` or `fix/<N>-short-description`.

    ```bash
    git switch -c issue-123-print-copies-squared upstream/master
    ```

4. **Commit and push to your fork.**

    ```bash
    git push -u origin issue-123-print-copies-squared
    ```

5. **Open a PR to upstream as a Draft while iterating.** Body must include `Closes #<N>`, a root-cause statement (see [Fix discipline](#1-fix-discipline)), and the staging verification evidence from [TESTING.md](TESTING.md).

    ```bash
    gh pr create \
      --repo Libki/libki-server \
      --base master \
      --head <your-account-or-org>:issue-123-print-copies-squared \
      --draft \
      --title "Fix: print job copies applied twice (Closes #123)" \
      --body-file ./pr-body.md
    ```

6. **Flip Draft → Ready for Review** once the fix-discipline checklist is met: reproduction captured, root cause stated, regression test added, staging verification attached.

### Trivial-change exemption

Typo fixes, comment-only changes, and `.perltidyrc` / `uncrustify` autoformat runs may skip the issue step. The PR body must state explicitly that the change is trivial and why no issue is needed.

### Never

- Push to `upstream/master` directly.
- Force-push to a branch that has open review comments without coordinating with reviewers first.
- Merge your own PR without at least one upstream maintainer's approval.

---

## 3. Staging before production

No code reaches a customer's production Libki installation without first being verified in a staging environment that mirrors the customer's relevant state (server version, client versions, print path, hardware emulators where possible).

If a bug can only be reproduced against a specific customer's environment, the fix workflow is:

1. Capture state from the customer environment (support-bundle / doctor output — see [TESTING.md](TESTING.md)).
2. Reproduce in staging using the captured state.
3. Fix, verify, then deploy.

---

## 4. API compatibility and versioning

- `/api/client/v1_0` is **legacy**. Do not extend it. New client-server features go on v2-style REST endpoints with per-action paths and proper role-based authorization. Template: [libki-server/lib/Libki/Controller/API/V2/Transactions.pm](libki-server/lib/Libki/Controller/API/V2/Transactions.pm).
- Existing v1 endpoints must remain backward-compatible. Deployed libki-client and libki-print-manager versions across the installed base assume v1 stays working.
- API changes that cross repo boundaries (e.g., new field in server response consumed by libki-client) require a coordinated release plan documented in the PR description.

---

## 5. Multi-tenancy and location scoping

- Every DB query in libki-server must scope by `instance`. Missing this is the most common mistake in this codebase.
- Every new feature must consider `location` scoping from day one. If a feature could reasonably differ between library branches, make it location-aware in the initial implementation. Retrofitting location-awareness is expensive.
- The allotment-by-location pattern (`Allotment` table + `TimeAllowanceByLocation` setting) is the template. Follow it.

Known candidates that currently live at `instance` scope and probably should move to `location` scope: max session length, dashboard default view, printer-visible-on-dashboard filters. Treat these as the first examples when you need a per-branch setting.

---

## 6. Money-moving operations

Any operation that debits or credits a patron's account — print release, fund addition, Jamex deposit, Stripe webhook — must:

1. Show explicit confirmation of the outcome (success or failure) to whoever initiated it (patron or staff).
2. Disable the triggering control during an in-flight request so it cannot be clicked repeatedly.
3. On failure, state the reason and what the user should do next (e.g., "Insufficient funds: $0.50 short. Add funds to continue.").
4. Never silently succeed or silently fail.

A patron hitting "print" four times because no confirmation appears, each time burning their balance, is a direct consequence of violating this rule.

---

## 7. Security baseline

- **SSL/TLS verification on by default.** If a deployment needs self-signed-cert support, make it an explicit per-setting opt-in with a log warning. Never a global disable. (libki-print-manager currently violates this; fix on next touch.)
- **No new plaintext secrets in config files.** Existing ones (libki-print-station backdoor credentials) are grandfathered but must not be extended. New auth flows use API keys or proper auth tokens.
- **No `eval()` or `QScriptEngine` on data received from the network.** Use `JSON.parse()` / `QJsonDocument`. One existing violation in libki-print-station/main.qml must be replaced when that file is next touched.
- **Passwords from SIP/LDAP are cached in the DB** as an artifact of legacy behavior. Do not add new paths that rely on this cache; delegate to the external auth on every request instead.
- **CSRF tokens required on POST/PUT/DELETE** server endpoints that change state.
- **Don't log secrets.** API keys, backdoor credentials, session tokens, patron passwords — none in log output, none in support-bundle exports.

---

## 8. Cross-repo changes

When a change requires coordinated updates across multiple Libki repos:

1. List all affected repos in the PR description.
2. Merge the server-side change first, with backward compatibility for old clients.
3. Roll out client changes after the server is live.
4. Document the version coupling in the CHANGELOG of each affected repo.

### Cross-Repo Tracking ID (CRTID)

Multi-repo efforts need a single identifier that links related issues and PRs across repos. A CRTID has the form:

```
<initiator-github-handle>-<YYYYMMDD>-<HHMM>
```

Example: `brendan1226-20260416-1327`.

**When to mint.** At the moment you open the first issue of a multi-repo effort. Include it at the top of that issue's body, on its own line:

> Cross-repo tracking: `brendan1226-20260416-1327`

**When to reuse.** Every subsequent issue or PR across any Libki repo that's part of the same effort includes the same CRTID at the top of its body, using the same line format.

**Why.** Searching `Libki` org issues and PRs for a CRTID surfaces every related thread in one query. The timestamp disambiguates when an initiator runs multiple efforts in parallel; the GitHub handle identifies who to ping for context.

**Optional.** Prefix branch names with `xrepo-<CRTID>-<short-description>` for the same searchability, e.g., `xrepo-brendan1226-20260416-1327-pdf-upload-path`.

**Single-repo changes do not need a CRTID.** The repo-local issue number is sufficient.

---

## 9. Diagnostics

Same-hardware, same-image deployments exhibit divergent bugs (partner-reported: Marina and Prunedale branches on identical images printing copies differently). This is environmental state that isn't being captured anywhere.

Aspirational: each client repo gains a `libki-doctor` command that dumps a redacted support bundle (app version, Qt version, OS version, INI state with secrets redacted, installed printer drivers, bundled-tool versions, recent log entries). See [TESTING.md](TESTING.md).

Until that exists: when diagnosing a field bug, explicitly record the environmental state you collected. "It works on my machine" is not a diagnosis.

---

## 10. Style and formatting

Enforced per repo:

- libki-server: `.perltidyrc` (100-col, 4-space indent, Perl Best Practices).
- libki-client, libki-print-manager: `uncrustify.cfg`.
- libki-print-station: Qt 6 defaults (4-space indent, camelCase methods, PascalCase types).

See each repo's `AGENTS.md` for details.

---

## 11. Non-goals

- Don't refactor surrounding code while fixing a bug. Isolated fixes make regressions easier to diagnose.
- Don't add abstractions, helpers, or "while we're here" cleanups without a specific requirement.
- Don't add feature flags or backwards-compatibility shims when the change can be made directly.
- Don't write new documentation files unless asked.
- Don't introduce new dependencies without justifying why the existing stack can't do the job.
