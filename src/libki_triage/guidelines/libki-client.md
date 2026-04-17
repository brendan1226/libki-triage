# AGENTS.md — libki-client

Instructions for AI agents and human contributors working in this repo. Read the ecosystem-wide [libki-coding-guidelines.md](../libki-coding-guidelines.md) first.

---

## Stack snapshot

- C++ with Qt 5.5 (Widgets, WebKit, Script, Network).
- qmake (`Libki.pro`).
- MinGW 4.9.2 32-bit on Windows; system compiler on Linux.
- Signal/slot event-driven architecture.
- `QSettings` (INI) for persistent config.
- Custom rotating-file logging in [logutils.h](logutils.h) / [logutils.cpp](logutils.cpp).

No automated tests. Manual verification against scenarios in [TESTING.md](../TESTING.md) is the bar.

---

## Contribution workflow

Fork → issue → branch → PR, per the ecosystem [libki-coding-guidelines.md §2](../libki-coding-guidelines.md#2-contribution-workflow). Upstream is `Libki/libki-client` (default branch: `master`). Open issues and PRs against upstream; push commits to your fork, never to the upstream repo.

---

## Non-negotiables

### JSON parsing

Use `QJsonDocument` / `QJsonObject`. **Do not use `QScriptEngine`** for parsing network responses. Existing `QScriptEngine` call sites in [networkclient.cpp](networkclient.cpp) are legacy; replace them when the surrounding code is next touched.

No `eval()` on network data anywhere.

### SSL verification

On by default. Do not globally disable SSL errors. If a deployment needs self-signed-cert support, expose it as an explicit per-setting opt-in with a log warning.

### Platform conditional compilation

Use `#ifdef Q_OS_WIN` / `#ifdef Q_OS_UNIX`. Keep platform-specific code contained inside these blocks; do not spread platform assumptions through business logic.

Windows-specific (documented for awareness, not invitation to extend): kiosk-shell replacement, `explorer.exe` management, `GetLastInputInfo` for inactivity, `on_startup.exe` / `on_login.exe` / `on_logout.exe` helpers.

### Version bump

The `VERSION` macro in [networkclient.cpp](networkclient.cpp) is broadcast to the server with every request. Bump it for each release — server metrics and admin UI surface it.

### Session continuity with the server

The server assumes the client re-registers every 10 seconds. Do not change that cadence without a coordinated server-side adjustment. If you add new periodic timers, keep them independent of the register-node timer so neither starves the other.

### Upload confirmation

The client uploads print jobs to the server. When an upload completes, confirm to the patron explicitly (success dialog or equivalent). When it fails, state the reason. Do not silently retry — silent retries hide the bug and confuse both patrons and staff.

---

## Style

[uncrustify.cfg](uncrustify.cfg) is the reference. Summary:

- Qt conventions: PascalCase classes, camelCase methods, UPPER_CASE constants.
- Traditional `#ifndef` include guards (not `#pragma once`) — follow the existing convention.
- Logging via `qDebug()` with ENTER/LEAVE markers at function boundaries. Keep this pattern for consistency with existing code.

---

## Build

Windows:

```
set QTDIR=C:\Qt\5.5\mingw492_32
qmake Libki.pro
mingw32-make
```

Linux:

```
qmake Libki.pro
make
# .deb build via deploy/linux/build_deb.sh
```

---

## Testing (manual)

See [TESTING.md](../TESTING.md). Scenarios relevant to this repo:

- Login: username+password, guest self-registration, passwordless mode.
- Logout (user-initiated, timeout, forced from server).
- Session timer: visible countdown, system-tray tooltip, end-of-session behavior.
- Inactivity auto-logout.
- Session lock + unlock with password.
- Print-upload from the client (spool monitoring + multipart HTTP).
- Reservation acknowledge.
- Connectivity loss and recovery (no crash, no silent failures).

---

## Known legacy areas

- Qt 5.5 is from 2015. WebKit is deprecated. Migration to Qt 6 is a latent task; until then, code as Qt 5.5.
- `QScriptEngine` / `QJsonDocument` coexist; migrate opportunistically.
- Verbose `XXXXXXXXXXXXXXXXXXX` debug markers in [loginwindow.cpp](loginwindow.cpp). Don't ship new ones; remove when touching surrounding code.
- Hardcoded Windows registry paths for kiosk-shell mode — don't refactor unless the whole kiosk-shell path is being revisited.
