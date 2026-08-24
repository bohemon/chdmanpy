# chdmanpy 0.1.0 release notes

chdmanpy 0.1.0 is the first packaged, pipeline-oriented release. It supports
Windows and Linux with Python 3.11 or later and installs the `chdmanpy` console
command through pipx or another isolated Python environment.

## Highlights

- `plan`, `run`, and `convert` provide inspectable direct and manifest-based
  CHDMAN workflows.
- stdout is BOM-free UTF-8 JSON Lines; diagnostics, runtime selection, and log
  locations are written to stderr.
- Bundled `others`, `ps2`, and `psp` presets allow an installed command to plan
  work without a repository checkout or user-supplied TOML.
- CHDMAN processes use a single bounded invocation-wide worker budget while
  result records remain in deterministic plan order.
- Outputs are created in private sibling staging, validated as CHD v5, and
  published without destructively overwriting an existing destination.

## Breaking CLI and archive migration

The historical `python chdmanpy.py INPUT OUTPUT --config FILE` interface is not
supported by 0.1.0. Replace it with `chdmanpy convert INPUT --output-dir OUTPUT`
and a bundled `--preset`, or continue using an existing strict `[options]` TOML
mapping through `--config`.

ZIP discovery and extraction have moved to the independently installed
[ArcShuttle](https://github.com/bohemon/ArcShuttle). chdmanpy neither extracts
archives nor invokes ArcShuttle. A finalized ArcShuttle 0.3.2 schema-v2 result
stream can be passed explicitly through `--arcshuttle-results`.

## Runtime and safety requirements

CHDMAN is an external prerequisite. chdmanpy discovers an explicitly selected
executable, `CHDMANPY_CHDMAN`, `[runtime].chdman`, or `PATH`; it does not
download or install CHDMAN during conversion.

Inputs and ArcShuttle output directories are never deleted, moved, or modified.
Existing destinations use explicit `fail`, `skip`, or deterministic `rename`
policy. Failed or interrupted owned staging is retained for inspection, and
CHDMAN stdout/stderr is isolated in per-job logs.

## Limitations

- 0.1.0 allows only the CHDMAN `createcd` and `createdvd` operations. Bundled
  presets cover CUE and ISO; a strict `[options]` table may map other file
  extensions to those operations.
- A CUE job records the primary CUE file's metadata identity; it does not claim
  that companion track files were parsed or integrity-protected.
- Archive cleanup, source deletion, destructive overwrite, automatic CHDMAN or
  ArcShuttle installation, GUI, watch service, and PowerShell module are out of
  scope.
- Real-CHDMAN integration is optional; the required test matrix uses one
  deterministic fake executable.

## Release verification

The release candidate is gated by `hatch run check`, `hatch build`, artifact
allowlist/denylist inspection, and an offline clean-wheel smoke test. The smoke
test verifies console and module entry points, bundled presets, direct
conversion, plan/run, ArcShuttle-result ingestion, historical `[options]`
configuration, logs, failures, process interruption, manifest collisions,
malformed upstream input, and preservation of pre-existing outputs. Required CI
covers Ubuntu and Windows on Python 3.11 and 3.14 and reports through the stable
aggregate `required` job.

Tagging, creating a GitHub Release, and uploading to an index remain explicit
maintainer actions after the verified candidate is merged.
