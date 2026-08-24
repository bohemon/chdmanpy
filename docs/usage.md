# chdmanpy usage and migration guide

[Japanese / 日本語](usage.ja.md) · [README](../README.md) ·
[JSON Lines schema v1](schema-v1.md)

## Installation and runtime requirements

chdmanpy supports Windows and Linux with Python 3.11 or later. The recommended
installation for a published release is:

```console
pipx install chdmanpy
```

For an offline or pinned installation, download the universal wheel from the
project's GitHub Release, verify its published SHA-256 digest, then install the
verified local file:

```console
pipx install ./chdmanpy-0.1.0-py3-none-any.whl
```

Use `pipx install .` in a source checkout. To use a conventional virtual
environment instead of pipx, create and activate it, then install the release
wheel with `python -m pip install ./chdmanpy-0.1.0-py3-none-any.whl`.

Upgrade a registry installation with `pipx upgrade chdmanpy`, or
`python -m pip install --upgrade chdmanpy` in an activated virtual environment.
Remove it with `pipx uninstall chdmanpy`, or `python -m pip uninstall chdmanpy`
in that environment. Both `chdmanpy` and `python -m chdmanpy` expose the same
interface.

CHDMAN is an external runtime prerequisite. Install it separately, then select
it with `--chdman`, `CHDMANPY_CHDMAN`, `[runtime].chdman`, or `PATH`, in that
order. chdmanpy does not download CHDMAN during a conversion and never installs
or invokes ArcShuttle.

The repository's `install-chdman.ps1` is an optional source-distribution helper,
not the chdmanpy installer. On Windows x64 or Arm64 it downloads the pinned MAME
0.287 package, verifies its recorded SHA-256 digest, and copies `chdman.exe` into
the directory containing the script. It does not update `PATH` or a pipx
environment, and it replaces an existing `chdman.exe` at that location. Review
the script before explicitly running it, then select the resulting executable
with `--chdman` or configuration. It is not included in the wheel and is never
run automatically.

## Commands and inputs

The three command families are:

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

`plan` validates inputs and emits schema-v1 `job` records without discovering or
running CHDMAN. `run` completely preflights a saved chdmanpy manifest before
running it. `convert` plans and runs in one invocation.

For `plan` and `convert`, select exactly one input form:

- one or more positional file or directory paths;
- `--files-from FILE` for newline-delimited paths;
- `--files0-from FILE` for NUL-delimited paths; or
- `--arcshuttle-results FILE` for an ArcShuttle 0.3.2 schema-v2 extract stream.

Use `-` only where an option explicitly documents stdin. chdmanpy never reads
stdin implicitly. ArcShuttle results are upstream execution records, not
chdmanpy manifests, and therefore cannot be passed to `run --manifest`.

Planning options are available on `plan` and `convert`:

| Option | Purpose |
| --- | --- |
| `--output-dir DIR` | Output root; required unless supplied by environment or TOML. |
| `--preset others|ps2|psp` | Select a bundled format preset; the default is `others`. |
| `--config FILE` | Read strict UTF-8 TOML configuration. |
| `--existing fail|skip|rename` | Existing-output policy; the default is `fail`. |
| `--priority INTEGER` | Record a signed 32-bit scheduling priority in the manifest. |
| `--on-upstream-error fail|skip` | Handle non-clean ArcShuttle results; valid only with `--arcshuttle-results`. |

Runtime options are available on `run` and `convert`; `run` also accepts
`--config FILE`:

| Option | Purpose |
| --- | --- |
| `--chdman COMMAND` | Select the CHDMAN executable. |
| `--workers COUNT` | Bound concurrent CHDMAN processes. |
| `--fail-fast` | Stop starting jobs after the first job failure. |
| `--allow-changed` | Run a changed primary input with a warning rather than failing it. |
| `--log-dir DIR` | Select the root for per-run and per-job logs. |

Run `chdmanpy COMMAND --help` for the authoritative option syntax.

## Direct and inspectable workflows

Convert a directory directly:

```console
chdmanpy convert ./input --output-dir ./chd --preset ps2 >results.jsonl
```

To inspect or edit the explicitly editable manifest fields before execution,
separate planning and running:

```console
chdmanpy plan ./input --output-dir ./chd --preset ps2 >jobs.jsonl
chdmanpy run --manifest jobs.jsonl >results.jsonl
```

The complete manifest is validated before CHDMAN is discovered or any job
starts. See the [schema-v1 contract](schema-v1.md) before editing a manifest.

## ArcShuttle workflows

chdmanpy does not discover or extract ZIP files. ArcShuttle owns archive
discovery, extraction, staging, and cleanup. A convenient direct pipeline in a
shell with `pipefail`, such as Bash or Zsh, is:

```bash
set -o pipefail
arcshuttle extract --output-dir ./extracted game.zip |
  chdmanpy convert --arcshuttle-results - --output-dir ./chd --preset ps2 \
  >results.jsonl
```

The equivalent PowerShell 7 pipeline is:

```powershell
& arcshuttle extract --output-dir .\extracted .\game.zip |
    & chdmanpy convert --arcshuttle-results - --output-dir .\chd --preset ps2 |
    Set-Content -Encoding utf8NoBOM .\results.jsonl
$pipelineSucceeded = $?
$chdmanpyStatus = $LASTEXITCODE
if (-not $pipelineSucceeded) { exit 1 }
exit $chdmanpyStatus
```

Save both `$?` and `$LASTEXITCODE` immediately after the PowerShell pipeline.
`$pipelineSucceeded` detects a `Set-Content` failure; `$chdmanpyStatus` preserves
the most recent native process status until another native process is run. These
direct forms are convenient, but `pipefail` only makes a supporting shell report
a failing pipeline. It does not communicate the producer exit to chdmanpy, nor
guarantee that downstream conversion did not start. ArcShuttle's schema-v2
summary also does not contain the producer process exit. If a clean ArcShuttle
process exit is required, capture and check it before conversion.

Safe POSIX handoff:

```sh
results=./arcshuttle-results.jsonl
if arcshuttle extract --output-dir ./extracted game.zip >"$results"; then
  chdmanpy convert --arcshuttle-results "$results" \
    --output-dir ./chd --preset ps2 >./results.jsonl
else
  arcshuttle_status=$?
  printf 'ArcShuttle failed with exit %s; conversion was not started.\n' \
    "$arcshuttle_status" >&2
  exit "$arcshuttle_status"
fi
```

For a byte-preserving PowerShell handoff, use PowerShell 7 and copy the native
stdout stream directly. This avoids depending on the text encoding used by
Windows PowerShell 5.1 or older PowerShell redirection:

```powershell
$arcResults = Join-Path $PWD "arcshuttle-results.jsonl"
$start = [System.Diagnostics.ProcessStartInfo]::new()
$start.FileName = "arcshuttle"
$start.UseShellExecute = $false
$start.RedirectStandardOutput = $true
foreach ($argument in @(
    "extract", "--output-dir", ".\extracted", ".\game.zip"
)) {
    [void] $start.ArgumentList.Add($argument)
}

$arcshuttle = [System.Diagnostics.Process]::Start($start)
$output = [System.IO.File]::Create($arcResults)
try {
    $arcshuttle.StandardOutput.BaseStream.CopyTo($output)
} finally {
    $output.Dispose()
}
$arcshuttle.WaitForExit()
if ($arcshuttle.ExitCode -ne 0) {
    throw "ArcShuttle failed with exit $($arcshuttle.ExitCode); conversion was not started."
}

& chdmanpy convert --arcshuttle-results $arcResults `
    --output-dir .\chd --preset ps2 > .\results.jsonl
$conversionSucceeded = $?
$chdmanpyStatus = $LASTEXITCODE
if (-not $conversionSucceeded) { exit 1 }
exit $chdmanpyStatus
```

The default `--on-upstream-error fail` rejects the complete ArcShuttle stream if
any result is not a finalized success or contains a warning. Explicit
`--on-upstream-error skip` may use only validated successful roots, reports every
omission on stderr, and still returns exit 1 if downstream conversion succeeds.
Malformed structure, inconsistent summaries, unsafe paths, and incomplete
staging are always rejected. See the normative
[ArcShuttle ingestion contract](arcshuttle-schema-v2.md).

## Streams, logs, staging, and exits

stdout is reserved for BOM-free UTF-8 JSON Lines:

- `plan` emits only `job` records.
- `run` and `convert` emit one ordered `result` per job, followed by exactly one
  `summary`.

Diagnostics, the selected CHDMAN/version, and the run-log path go to stderr.
Execution events are written to the run log rather than streamed as progress.
CHDMAN stdout and stderr are captured in the `log_path` recorded for each
result. By default logs are placed in a `.chdmanpy-logs` tree beneath the first
planned destination's parent; use `--log-dir` to choose another root.

JSON Lines consumers must read through EOF and require the terminal `summary`;
an early successful `result` does not mean the invocation completed. Result
statuses mean:

- `success`: the verified CHD was published cleanly;
- `warning`: the job completed but reported a warning;
- `failed`: the job failed and may report retained owned staging;
- `skipped`: the job was intentionally not executed; and
- `interrupted`: interruption prevented or stopped completion.

Each conversion writes into a private sibling `.failed` staging directory and
publishes a verified CHD without overwriting the destination. Successful owned
staging is removed. Failed or interrupted owned staging is retained for
inspection and its absolute path is reported as `staging_path`; chdmanpy never
modifies an input or ArcShuttle output directory.

Existing destinations use the manifest's explicit policy: `fail` (default),
`skip`, or deterministic `rename`. No policy destructively overwrites a CHD.

| Exit | Meaning |
| ---: | --- |
| 0 | Clean success. |
| 1 | Completed warning or skipped work, including an accepted partial upstream run. |
| 2 | One or more CHDMAN jobs failed. |
| 64 | Usage, configuration, input, stream, or manifest error; no job starts after a failed preflight. |
| 130 | Interrupted. Valid result records and a summary may accompany this exit after execution began. |

Valid result records followed by a summary may accompany exits 1, 2, and 130.

## Configuration

Configuration precedence is CLI, `CHDMANPY_*` environment, explicit TOML, then
bundled preset/defaults. Unknown keys and invalid types are errors.

```toml
[options]
".cue" = ["createcd"]
".iso" = ["createdvd", "-c", "zlib"]

[planning]
output_dir = "./chd"
existing = "fail"
priority = 0

[runtime]
chdman = "chdman"
```

The bundled preset mappings are:

| Preset | Extension | CHDMAN creation arguments |
| --- | --- | --- |
| `others` | `.cue` | `createcd` |
| `ps2` | `.cue` | `createcd` |
| `ps2` | `.iso` | `createdvd -c zlib` |
| `psp` | `.iso` | `createdvd -hs 2048 -c zstd` |

A historical `[options]` table remains accepted and completely replaces the
selected preset's extension mapping. Input/output/force arguments are managed by
chdmanpy and cannot be supplied through TOML.

The supported environment variables are:

| Variable | Accepted value and meaning |
| --- | --- |
| `CHDMANPY_OUTPUT_DIR` | `<path>`: nonempty output root, equivalent to `--output-dir`. |
| `CHDMANPY_EXISTING` | `fail` / `skip` / `rename`, equivalent to `--existing`. |
| `CHDMANPY_PRIORITY` | `-2147483648..2147483647`: decimal integer, equivalent to `--priority`. |
| `CHDMANPY_PRESET` | `others` / `ps2` / `psp`, equivalent to `--preset`. |
| `CHDMANPY_CHDMAN` | `<executable-name-or-path>`: one nonempty executable name or path, used before `PATH`; shell fragments and arguments are not accepted. |

## Migration from the historical script

The historical `python chdmanpy.py INPUT OUTPUT --config FILE` interface is not
a 0.1.0 compatibility surface.

| Historical behavior | 0.1.0 replacement |
| --- | --- |
| `python chdmanpy.py INPUT OUTPUT --config ps2.toml` | `chdmanpy convert INPUT --output-dir OUTPUT --preset ps2` |
| A custom `[options]` TOML | Continue using `--config FILE`; `[options]` remains supported. |
| ZIP discovery and extraction | Run ArcShuttle separately and pass `--arcshuttle-results`; chdmanpy has no archive backend. |
| `--temp-dir`, `unzip_zip_files`, and `_extracted` output | No chdmanpy replacement; extraction policy belongs to ArcShuttle, and outputs preserve namespaced relative paths. |
| `[run].workers` | Use the invocation-wide `--workers COUNT`. |
| Human-readable progress/results on stdout | Consume JSON Lines from stdout; read diagnostics from stderr and CHDMAN output from result log paths. |
| A `chdman.exe` beside `chdmanpy.py` | Use `--chdman`, `CHDMANPY_CHDMAN`, `[runtime].chdman`, or `PATH`. |
