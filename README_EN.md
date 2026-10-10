[中文](README.md)

# Lumen · 知页

A local HTML knowledge library for reading, editing and keeping useful work. The native Mac window, CLI, on-demand local API and MCP share the same engine and version rules.

Build portals, reports and teaching pages from JSON; import HTML; edit text, comments and notes; search chapters; compare, restore and export versions. PPTX conversion preserves slide order, text, tables and images; complex objects require review. Directory graphs need the separately installed Folio tool.

This standalone source snapshot includes fictional examples, without the author's knowledge library, real deliverables, internal records or deployment configuration.

## Use from source

Requires Python 3.12 or newer. The Mac window requires macOS 14 or newer and Xcode Command Line Tools.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python engine/entry.py --state-dir ./state status --json
.venv/bin/python engine/entry.py --state-dir ./state build --content examples/content.json --json
.venv/bin/python engine/entry.py --state-dir ./state list --json
.venv/bin/python engine/entry.py guide overview --json
bash build.sh
```

The result's `entry_path` identifies the HTML entry. Use `--state-dir` to isolate tests; the default is the current user's `~/Library/Application Support/Lumen`. Source documents are preserved.

`build/Lumen.app` contains a frozen Python engine and runs without the source checkout. Open the app from the build directory. The build makes an ad-hoc local test candidate only: it does not install, notarize or publish. Downloadable Mac packages require separate Developer ID signing, Apple notarization and stapling. This preparation snapshot has no public download package.

## Generation and optional capabilities

`generate` uses an official `codex` or `claude` CLI already installed and authenticated by its user. Models follow each CLI's configuration, without a fixed model tier. Select the default with `LUMEN_PROVIDER=codex` or `claude`, or pass `--provider` per request. Missing CLI/login fails explicitly; Lumen does not log in for the user or use the author's accounts, tokens or routing settings.

```sh
.venv/bin/python engine/entry.py agent-status --json
.venv/bin/python engine/entry.py --state-dir ./state generate --source examples/source.md --request 'Explain the purpose and steps of this fictional project' --provider codex --json
```

Generation sends selected source text to that provider. Offline builds, imports, reading and saving do not need a model. `LUMEN_LLM_CLIENT_DIR` can select a compatible adapter implementing `llm_client.provider_settings()` and `chat(...)`.

Discovery reads `~/Documents/Lumen` by default, or the folders passed to `discover --roots`; it does not automatically import or scan the entire computer. The API starts only on explicit `serve` and requires a token; MCP uses stdio. Configuration transfer and opt-in iCloud sync include portable settings only. Knowledge content, source paths and model credentials stay on the device.

Update checking uses this repository's [GitHub Releases](https://github.com/zengtianli/lumen-oss/releases). The distributable package still awaits signing, notarization and stapling. There is no public release yet, so update checks may report no release.

## Verification and limits

```sh
.venv/bin/python scripts/parallel-tests.py --jobs 12
```

Tests use isolated directories and fictional fixtures without real model calls. The optional graph integration test skips when Folio is absent; core tests continue. Local substitutes verify model arguments, timeouts and failures. A source build does not prove real online generation, manual window interactions or release-grade signing. Scanned PDFs require OCR; Word extraction reads main-document paragraphs and tables. External scientific plotting, advanced review, speech and deployment tools are not included.

Original code is [MIT licensed](LICENSE). See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for dependencies.
