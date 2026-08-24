# chdmanpy Repository Instructions

This file applies to the entire repository. A more specific `AGENTS.md` may add
rules for a subtree, but it must not weaken the interface, safety, compatibility,
or verification requirements below.

## Project direction

- The product, Python distribution, primary package, and console command are all
  named **chdmanpy**. The first packaged release target is version 0.1.0.
- Support Windows and Linux with Python 3.11 or later.
- `chdman` is an external runtime prerequisite. Discover it from an explicit CLI
  option, configuration, or `PATH`; do not download or install it during an ordinary
  conversion command.
- ArcShuttle owns archive discovery, extraction, and extraction scheduling. Do not
  add ZIP handling, another archive backend, or code that invokes ArcShuttle from
  inside chdmanpy.
- Integrate with ArcShuttle through its documented schema-v2 JSON Lines result
  stream. Keep the two tools independently installable and releasable.
- The historical `python chdmanpy.py INPUT OUTPUT --config ...` interface is not a
  compatibility surface for 0.1.0. Document its replacement. Continue accepting the
  existing `[options]` TOML mapping unless an issue explicitly defines and documents
  a migration.

## Target architecture

Use a `src/chdmanpy` package and keep responsibilities separated:

- `cli`: argument parsing and stream orchestration only;
- `config`: defaults, bundled presets, TOML, environment, and CLI precedence;
- `input`: explicit path-list and ArcShuttle-result ingestion;
- `planner`: deterministic discovery and job derivation;
- `manifest`: JSON Lines schemas, integrity, and complete preflight validation;
- `chdman`: executable discovery and safe subprocess argument construction;
- `runner`: bounded parallel execution, interruption, staging, and finalization;
- `results`: result/summary records and process-exit policy.

Do not make `cli` a second implementation of the planner or runner. Keep filesystem
policy out of presentation code, and keep subprocess details behind the `chdman`
adapter so tests can use a deterministic fake executable.

## Public CLI and pipeline contract

The target command families are:

```text
chdmanpy plan [OPTIONS] PATH...
chdmanpy plan [OPTIONS] --files-from FILE
chdmanpy plan [OPTIONS] --files0-from FILE
chdmanpy plan [OPTIONS] --arcshuttle-results FILE
chdmanpy run --manifest FILE [OPTIONS]
chdmanpy convert [OPTIONS] PATH...
chdmanpy convert [OPTIONS] --files-from FILE
chdmanpy convert [OPTIONS] --files0-from FILE
chdmanpy convert [OPTIONS] --arcshuttle-results FILE
```

`FILE` may be `-` only where an option explicitly documents stdin. Positional paths,
`--files-from`, `--files0-from`, and `--arcshuttle-results` are mutually exclusive;
never read stdin implicitly.

The primary ArcShuttle pipeline is:

```sh
arcshuttle extract --output-dir EXTRACTED ARCHIVE... |
  chdmanpy convert --arcshuttle-results - --output-dir CHD_OUTPUT --preset ps2
```

An inspectable workflow uses `chdmanpy plan` and `chdmanpy run --manifest`. ArcShuttle
results are upstream execution records, not chdmanpy job manifests; never pass them
directly to `run`.

- Reserve stdout for UTF-8 JSON Lines. Send diagnostics, selected executable/version,
  and progress to stderr.
- `plan` emits job records only. `run` and `convert` emit one result per job followed
  by exactly one summary record.
- Read an input stream through EOF and validate the complete stream before starting
  any CHDMAN job. Reject malformed records, duplicate jobs, output collisions, missing
  or inconsistent summaries, and unsupported schema versions without partial work.
- ArcShuttle ingestion accepts schema-v2 `extract` results only. Only finalized
  successful `output_path` directories are eligible as source roots. A warning,
  skipped, failed, or interrupted upstream result must never be treated as a finalized
  directory. Default to rejecting a partial upstream run; any opt-in policy that uses
  successful subsets must be explicit, diagnostic, and return a non-success status.
- Use process exits 0 for clean success, 1 for completed warnings/skips, 2 for job
  failure, 64 for usage/configuration/input/manifest errors, and 130 for interruption.
  Valid result records and a summary may accompany exits 1, 2, and 130.
- Preserve deterministic plan order in emitted records even when jobs complete out of
  order.

## Planning and path invariants

- Normalize relative user paths from the process working directory and preserve the
  first occurrence when deduplicating.
- Recursively inspect directory inputs without following symlinks, junctions/reparse
  points, devices, sockets, or other non-regular entries. Process only extensions
  declared by the selected preset or configuration.
- Namespace outputs by their explicit input root and preserve relative subdirectories.
  Do not flatten unrelated roots into one directory. Reject any remaining destination
  collision before execution, including case-insensitive collisions on Windows.
- Plans must use absolute source and destination paths and record enough source identity
  to detect a changed primary input before execution. Do not claim that a CUE file's
  identity covers companion track files unless those references are explicitly parsed,
  validated, and included.
- Keep destination and scheduling fields intentionally editable only if the manifest
  contract names them. Integrity-protect every other field and reject unsupported edits.
- Validate all configured CHDMAN operations and arguments. The operation must be an
  allowed CHDMAN creation command, and user options must not supply or replace the
  managed input/output arguments.

## Execution and safety invariants

- Never delete, move, or modify an input file or an ArcShuttle output directory.
- Never destructively overwrite an existing CHD. Existing-output behavior must be an
  explicit `fail`, `skip`, or deterministic `rename` policy, with `fail` as the default.
- Write each CHD to a private sibling staging path, verify the required CHDMAN success
  conditions, recheck destination absence, and publish atomically on the same filesystem.
  Retain failed owned staging for inspection; never rename or remove an unowned path.
- Invoke CHDMAN with an argument array, `shell=False`, and closed stdin. Never build a
  shell command from filenames or expose arbitrary shell fragments through TOML.
- Enforce one invocation-wide process budget. Do not multiply concurrency in nested
  helpers. Stop new starts after fail-fast or interruption and terminate only child
  processes owned by the current run using Windows- and Linux-appropriate mechanisms.
- Capture per-job stdout/stderr in logs rather than mixing CHDMAN output with the JSON
  Lines stream. Do not include secrets or unrestricted environment dumps in results or
  logs.
- ZIP extraction, archive cleanup, source deletion, destructive overwrite, a GUI, and
  automatic CHDMAN installation are outside the 0.1.0 scope.

## Configuration and packaging

- Package with `pyproject.toml` and a PEP 517 backend. Expose both `chdmanpy` and
  `python -m chdmanpy` entry points and verify installation through `pipx` or an
  equivalent clean isolated environment.
- Keep runtime dependencies empty unless a dependency is justified in its issue,
  documented, and covered by clean-wheel tests.
- Bundle maintained presets needed to replace the repository-root `ps2.toml`,
  `psp.toml`, and `others.toml` examples. A user-supplied config must not be required
  for the first successful installed-command smoke test.
- Configuration precedence is CLI, `CHDMANPY_*` environment, explicit TOML, bundled
  preset/defaults. Unknown keys and invalid types are usage errors; do not silently
  ignore misspellings.
- Build universal Python wheels only. Do not bundle a platform-specific CHDMAN binary
  in the wheel.

## Tests and required verification

- Tests must run on Windows and Linux without a real CHDMAN installation. Extend one
  deterministic fake CHDMAN for subprocess, logging, warning/failure, interruption,
  Unicode/space path, and staging tests.
- A real-CHDMAN integration test may only be optional and skip when the executable is
  unavailable.
- Cover POSIX and Windows path behavior explicitly, including separators, drive-qualified
  paths where applicable, Unicode, spaces, case-insensitive collision keys, and process
  interruption.
- Add a regression test with every bug fix. Do not weaken assertions, coverage, or the
  supported matrix to make a check pass.
- Run checks in proportion to a change and run the complete gate before closing an
  implementation issue:

```sh
hatch run check
hatch build
```

- Packaging, entry-point, preset, or documentation-inclusion changes also require a
  clean-wheel installation and CLI smoke test.
- GitHub Actions must cover the supported Windows/Linux and Python matrix. A stable
  aggregate `required` job must fail unless every matrix job succeeds.

## Branching and integration

Use issue-driven, trunk-based development. `main` is the only permanent branch and must
remain green and releasable.

The 0.1.0 implementation sequence is tracked by roadmap issue #1 and its dependency-linked
children. Do not reopen completed roadmap stages merely because the historical list remains;
track later work in a new issue with its own dependencies.

1. Start from a clean, current `main` and preserve all user changes.
2. Give every implementation change a GitHub issue with scope, dependencies, non-goals,
   and acceptance criteria.
3. Use one short-lived branch per reviewable change: `codex/<issue>-<slug>` for Codex
   work, or `feat/`, `fix/`, `docs/`, `test/`, and `chore/` as appropriate.
4. Deliver changes through pull requests; do not push implementation commits directly
   to `main`.
5. Require the complete supported CI matrix, the stable `required` check, an up-to-date
   branch, and resolved review conversations before merge.
6. Prefer squash merge and delete the merged branch.

Use `Refs #N` for intermediate pull requests and `Closes #N` only for the final pull
request that satisfies the issue. Separate mechanical package moves from behavior changes,
and do not mix unrelated cleanup into a feature pull request. Do not create, commit, push,
or change repository protection unless the user explicitly requests that external action.

## Documentation and release discipline

- Keep README concise: identity, requirements, recommended installation, quick start,
  the ArcShuttle pipeline, and links to normative manuals.
- Keep English and Japanese command/installation documentation semantically aligned when
  both exist. Update documentation coverage when commands, options, schemas, presets,
  environment variables, stream contracts, or exits change.
- Clearly document that chdmanpy no longer extracts ZIP files and does not invoke
  ArcShuttle. Show both a direct-directory workflow and safe Windows/POSIX pipeline or
  intermediate-file workflows.
- Tag releases only from a verified `main`. Build and inspect wheel/sdist contents,
  install the wheel into a clean environment, and smoke-test the console and module
  entry points before publishing. Publishing and repository-setting changes remain
  explicit maintainer actions unless separately authorized.
