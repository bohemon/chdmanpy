# chdmanpy JSON Lines schema v1

This document is normative for chdmanpy schema v1. Every stream is BOM-free UTF-8
JSON Lines. Blank lines, duplicate JSON object keys, non-object records, malformed
JSON, non-finite numbers, unknown fields, and unknown schema versions are errors.
Input is read through EOF and completely validated before work starts. stdout is
reserved for JSON Lines; diagnostics and progress belong on stderr.

## Job manifest

`plan` emits only `job` records. `run --manifest FILE` consumes only these records;
ArcShuttle result records are a distinct contract and are not interchangeable.
An empty manifest is invalid. All fields below are required, and no additional fields
are accepted:

```json
{
  "schema_version": 1,
  "record_type": "job",
  "job_id": "24 lowercase hexadecimal digits",
  "plan_index": 0,
  "source": {
    "path": "/absolute/source.cue",
    "input_root": "/absolute/input/root",
    "size": 1234,
    "mtime_ns": 1700000000000000000,
    "identity": "sha256: followed by 64 lowercase hexadecimal digits"
  },
  "destination": {"path": "/absolute/output.chd", "existing": "fail"},
  "chdman": {"operation": "createcd", "options": ["-c", "zstd"]},
  "scheduling": {"priority": 0, "estimated_weight": 1234},
  "tags": ["user-editable"],
  "warnings": [],
  "integrity": "sha256: followed by 64 lowercase hexadecimal digits"
}
```

`destination.existing` is `fail`, `skip`, or `rename`; the default is `fail`.
`chdman.operation` is `createcd` or `createdvd`. Options must not set managed input,
output, or force arguments. Integer fields do not accept JSON booleans. Sizes, times,
plan indexes, and estimated weights are nonnegative. Priority is a signed 32-bit
integer. The source path must be lexically equal to or beneath the input root under
the current host's path rules. Non-device Windows paths reject components ending in a
period or space because Win32 aliases those names. Option, tag, and warning strings are
nonempty.

The job ID is the first 24 lowercase hexadecimal digits of SHA-256 over canonical
JSON containing `schema_version`, the host-normalized `source_path`,
`source_identity`, `operation`, and `options`. Canonical JSON uses sorted keys,
compact separators, and UTF-8.

Integrity is `sha256:` plus SHA-256 over the canonical complete record after removing
`integrity`, `destination.path`, `scheduling.priority`, and `tags`. Those three data
fields are the complete external edit allowlist. Edited values are still subject to
ordinary type, range, absolute-path, duplication, and collision validation. Complete
manifest preflight also rejects every destination that is equal to or beneath any
job's `source.input_root`, using the current host's path rules. Plan indexes must be
unique and contiguous from zero; input record order may change, but validated jobs are
returned in plan-index order.

The fixture at `tests/fixtures/job-v1.jsonl` contains every schema-v1 job field,
including Unicode, quotes, spaces, backslashes, and a JSON-escaped newline.

## Result and summary stream

`run` and `convert` emit one `result` for every job, in ascending `plan_index` order,
then exactly one terminal `summary`. All listed fields are required; fields explicitly
shown as `null` are nullable.

```json
{"schema_version":1,"record_type":"result","run_id":"opaque-run-id","job_id":"24 lowercase hex digits","plan_index":0,"status":"success","source_path":"/absolute/source.cue","output_path":"/absolute/output.chd","staging_path":null,"log_path":"/absolute/job.log","chdman_exit_code":0,"started_at":"2026-08-24T01:02:03Z","finished_at":"2026-08-24T01:02:04Z","duration_ms":1000,"error":null,"warnings":[]}
{"schema_version":1,"record_type":"summary","run_id":"opaque-run-id","total":1,"success":1,"warning":0,"failed":0,"skipped":0,"interrupted":0,"duration_ms":1000}
```

Statuses are `success`, `warning`, `failed`, `skipped`, and `interrupted`. Paths are
absolute. Staging and log paths, CHDMAN exit code, timestamps, and error may be null.
Timestamps, when present, are RFC 3339 UTC values ending in `Z`; each start/end pair
must be both present or both null. Their exact accepted form is
`YYYY-MM-DDTHH:MM:SS(.fraction)?Z`. Result plan indexes form the complete ordered range
from zero through `total - 1`. A summary contains every status count. The counts must
sum to `total` and exactly match the preceding result records.

## Process exits

Exit precedence is interruption (130), any failed job (2), any warning status,
skipped job, or per-result warning text (1), then clean success (0). Invalid command
usage, configuration, input, manifest, or stream data exits 64 without starting a job.
