# AGENTS.md — libki-print-station

Instructions for AI agents and human contributors working in this repo. Read the ecosystem-wide [libki-coding-guidelines.md](../libki-coding-guidelines.md) first.

---

## Stack snapshot

- C++ with Qt 6.9 (Quick/QML).
- qmake6 (`LibkiPrintStation.pro`).
- MinGW 64-bit on Windows; system compiler on Linux.
- QML for UI: [main.qml](main.qml), [PaymentWindow.qml](PaymentWindow.qml), [LibkiBalance.qml](LibkiBalance.qml), [PrintRelease.qml](PrintRelease.qml).
- C++ backend ([backend.cpp](backend.cpp)) for Jamex hardware integration via `QLibrary`-loaded `JPClibs`.
- `QSettings` (INI) for config.
- Custom rotating-file logging in [logutils.cpp](logutils.cpp).

No automated tests. Manual verification per [TESTING.md](../TESTING.md).

---

## Contribution workflow

Fork → issue → branch → PR, per the ecosystem [libki-coding-guidelines.md §2](../libki-coding-guidelines.md#2-contribution-workflow). Upstream is `Libki/libki-print-station` (default branch: `main`). Open issues and PRs against upstream; push commits to your fork, never to the upstream repo.

---

## Non-negotiables

### Jamex hardware abstraction

The current [backend.cpp](backend.cpp) calls `jamexConnect()` on every balance read. Partner-reported symptom: Jamex stops recognizing coins mid-session, only a PC restart fixes it. The connect-on-every-read pattern is almost certainly a contributor.

Target pattern for new and touched code:

1. Single persistent connection opened at startup.
2. Health-check before each operation. If unhealthy, reconnect with bounded backoff.
3. Distinguish transient errors (reconnect) from fatal errors (surface to UI, stop).
4. Connection state explicitly tracked in a state machine, not inferred from return values.

When touching [backend.cpp](backend.cpp), move toward this pattern. Do not add more connect-on-every-call paths.

### No `eval()` on network responses

[main.qml](main.qml) currently parses a network response with `eval('new Object(' + responseText + ')')`. This is a code-injection vector. Replace with `JSON.parse(responseText)` when that file is next touched. No new `eval` calls anywhere in this repo.

### Money-moving confirmation

Every fund transfer (Jamex → Libki account, account → print release) must:

1. Show explicit success or failure to the patron.
2. Disable the triggering button during the in-flight request.
3. On insufficient funds, state the exact shortfall (e.g., "You need $0.50 more to print this job").
4. On Jamex deduction failure after a successful API debit, surface the rollback outcome clearly.

Partner-reported bug: patrons click print multiple times because nothing visible happens, burning their balance on each click. This rule is not optional.

### Total cost before print release

[PrintRelease.qml](PrintRelease.qml) must show the complete cost (`copies × per-page × pages`) before the patron commits to release. Per partner report, this is currently missing for multi-copy jobs.

### Kiosk-mode constraints

`client/prevent_exit` blocks window close. INI-stored backdoor credentials exist as a legacy exit path. **Do not extend the backdoor mechanism.** Do not log backdoor values. Do not add new plaintext secrets to the INI — use API keys or a proper auth flow.

### SSL verification

On by default. No global ignore, no per-reply ignore-all.

### Internationalization

UI strings use `qsTr()`. Translation catalog `LibkiPrintStation_en_US.ts` is the only populated one today, but new strings must still go through `qsTr()` so future locales can be added without retrofitting.

---

## Style

- Qt 6 QML conventions: camelCase ids, PascalCase components, `on*` signal handlers.
- C++: camelCase methods, `m_` prefix for private members, 4-space indent.
- Signals and slots use the Qt 5+ function-pointer syntax (`connect(obj, &Class::sig, ...)`); do not use the legacy `SIGNAL()`/`SLOT()` macros in new code.

---

## Build

Windows (matches CI):

```
qmake6 LibkiPrintStation.pro
mingw32-make
windeployqt6 --force --release --qmldir .. LibkiPrintStation.exe
```

Linux: requires Jamex `.so` symlinks per [`jamex/Linux (x64)/README.txt`](jamex/Linux%20(x64)/README.txt).

---

## Testing

Real Jamex hardware required for full verification. A Jamex emulator would unblock CI testing; flagged as aspirational in [TESTING.md](../TESTING.md).

Scenarios relevant to this repo:

- Login.
- Jamex deposit crediting the Libki account.
- Multi-copy job with full cost displayed before release.
- Insufficient-funds path with exact shortfall message.
- Jamex disconnect / reconnect within 30s without a process restart.
- Backdoor exit works (for kiosk deployment verification) but leaves no secrets in logs.

---

## Known legacy areas

- `eval()` parsing in [main.qml](main.qml) — replace on next touch.
- Connect-on-every-call Jamex pattern in [backend.cpp](backend.cpp) — migrate on next touch.
- Two-fund-source logic in [PrintRelease.qml](PrintRelease.qml) is complex; audit carefully when changing.
- Opaque `void*` handles from JPClibs — if you touch the hardware layer substantively, wrap them in a typed RAII class.
