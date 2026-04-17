# AGENTS.md — libki-print-manager

Instructions for AI agents and human contributors working in this repo. Read the ecosystem-wide [libki-coding-guidelines.md](../libki-coding-guidelines.md) first.

---

## Stack snapshot

- C++ with Qt 5.5 (Widgets, Network).
- qmake (`LibkiPrintManager.pro`).
- MinGW 4.9.2 32-bit. **Windows only.**
- System-tray daemon; no main window.
- Polling-based: every 10 seconds, HTTP GET for the next pending job.
- Bundled SumatraPDF for actual printing.
- `QSettings` (INI) for config; custom rotating-file logging.

~527 LOC total across four modules ([networkclient.cpp](networkclient.cpp), [systemtray.cpp](systemtray.cpp), [logutils.cpp](logutils.cpp), [main.cpp](main.cpp)). No automated tests. Manual verification per [TESTING.md](../TESTING.md).

---

## Contribution workflow

Fork → issue → branch → PR, per the ecosystem [libki-coding-guidelines.md §2](../libki-coding-guidelines.md#2-contribution-workflow). Upstream is `Libki/libki-print-manager` (default branch: `master`). Open issues and PRs against upstream; push commits to your fork, never to the upstream repo.

---

## Non-negotiables

### SSL verification — fix on touch

Current code globally ignores all SSL errors in [networkclient.cpp](networkclient.cpp) (around lines 220-222). This is a security bug. When modifying this file, replace the global ignore with:

1. Default: full SSL verification.
2. An explicit configuration option to allow specific self-signed fingerprints.
3. A log warning when the opt-in is active.

Do not propagate the current pattern into new code.

### Copies-metadata contract

Partner-reported bugs: Marina prints `copies²` copies (the "squared²" bug), Prunedale can't print multiple copies at all on identical hardware/image. The contract between server and print-manager on the `copies` field must be crystal clear:

- Server stores `copies` on the `PrintJob` row as the patron-intended count.
- Server does **not** pre-expand pages by `copies` in the PDF handed to print-manager.
- Print-manager passes `copies` to SumatraPDF via `-print-settings "...,{copies}x"`.
- SumatraPDF expands pages; the physical printer receives `pages × copies` output.

If any step multiplies twice, you get the squared² bug. Before changing any copies-handling code, verify end-to-end that each step applies the multiplier exactly once. Write a regression test that pins the contract.

### SumatraPDF invocation

Current command shape (in [networkclient.cpp](networkclient.cpp) around lines 178-186):

```
SumatraPDF.exe -silent -print-settings "{chroming},{plexing},{copies}x" -print-to "{physical_printer}" "{file}"
```

Gotchas that must not be re-introduced:

- **The copies flag requires an `x` suffix.** Non-obvious. Don't remove it.
- **Spaces in printer names or file paths** caused hangs until v1.3.1. Always quote both arguments.
- **`waitForFinished(-1)` is intentional** — large jobs take longer than any default timeout. Don't add a timeout without an explicit reason.
- **One job at a time.** The blocking wait prevents concurrency; don't try to parallelize without reworking the whole loop.

### Polling interval

Hardcoded to 10 seconds in [networkclient.cpp](networkclient.cpp) around line 34. In-code TODO asks for it to become configurable. If you need to change the interval, make it an INI setting rather than changing the constant.

### Three `QNetworkAccessManager` instances

Separate managers for polling, file download, and status updates. This sidesteps Qt's per-manager request serialization. Don't consolidate them without verifying that concurrent operations still work correctly.

---

## Style

- Qt 5.5 conventions.
- Mix of legacy `SIGNAL()`/`SLOT()` macros and modern function-pointer connects exists in-tree; prefer the modern form in new code.
- camelCase methods, PascalCase classes.

---

## Build

```
qmake LibkiPrintManager.pro
mingw32-make
```

Produces `release/LibkiPrintManager.exe`. Installer built via Inno Setup 5 from [deploy/windows/](deploy/windows/).

---

## Testing

Real Windows printer required. Scenarios that must be kept passing (see [TESTING.md](../TESTING.md)):

- Single-copy job.
- Multi-copy job sourced from direct PDF.
- **Multi-copy job sourced from Microsoft Word.** Distinct bug class from direct PDF — the two must be tested separately.
- Job with spaces in the printer name.
- Job with spaces in the file path.
- Network blip during poll, auto-recovery on next poll.
- Server offline at startup; recovery when server comes back.

---

## Known legacy areas

- Global SSL-error ignore — fix on next touch of [networkclient.cpp](networkclient.cpp).
- Hardcoded polling interval — make configurable when surrounding code is next modified.
- `QNetworkReply::setProperty()` used to smuggle job JSON between async callbacks — unusual but works; don't refactor speculatively.
- `waitForFinished(-1)` blocking model — intentional. Don't "improve" it without a plan that covers the whole architecture.
