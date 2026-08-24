# ArcShuttle schema-v2 result ingestion

This document is normative for `--arcshuttle-results`. The accepted producer shape is
the ArcShuttle 0.3.2 schema-v2 execution stream. It is not a generic JSON import and it
is not a chdmanpy job manifest. chdmanpy does not import, discover, install, configure,
or invoke ArcShuttle.

The input is a BOM-free UTF-8 JSON Lines stream read through EOF. Blank lines,
duplicate JSON keys, malformed JSON, unknown fields, and trailing records are errors.
One or more `result` records must be followed by exactly one terminal `summary`.
Every record must use schema version 2; every result must use operation `extract`.

## Accepted ArcShuttle 0.3.2 records

All fields are required. No additional fields are accepted.

```json
{"schema_version":2,"record_type":"result","run_id":"20260824T064152Z-796729f7","job_id":"402e72e71dc2221c1e433f99","path":"/archives/space name.zip","status":"success","exit_code":0,"started_at":"2026-08-24T06:41:52.011Z","finished_at":"2026-08-24T06:41:52.012Z","duration_ms":1,"assigned_cpu_tokens":1,"assigned_threads":1,"output_dir":"/extracted/space name","staging_dir":null,"log_path":"/logs/run/job","warnings":[],"operation":"extract","output_path":"/extracted/space name","staging_path":null}
{"schema_version":2,"record_type":"summary","run_id":"20260824T064152Z-796729f7","total":1,"success":1,"warning":0,"failed":0,"skipped":0,"interrupted":0,"duration_ms":3}
```

`output_dir` and `output_path` must be identical. `staging_dir` and `staging_path`
must also be identical. Run IDs must match, job IDs must be unique 24-character
lowercase hexadecimal values, output paths must be unique under host path rules, and
summary totals and all five status counts must exactly match the results. Assigned
threads must not exceed assigned CPU tokens.

Status-dependent fields follow ArcShuttle 0.3.2 exactly. Success uses exit code 0 and
null staging aliases. Warning uses exit code 1 and non-null matching staging aliases.
Failed may use a null exit, exit 0, or another failure exit, but never exit 1. Skipped
uses a null exit and null staging aliases. Interrupted permits both unstarted null
values and the exit/staging values observed after a process was started.

Paths use the producer host's native absolute syntax. POSIX streams contain paths such
as `/data/extracted/game`; Windows streams contain drive-qualified or UNC paths whose
backslashes are JSON-escaped. A result file is not defined to be portable between
operating-system path syntaxes.

## Finalized roots and upstream policy

A planner root must come from a result with operation `extract`, status `success`,
exit code 0, matching output aliases, null staging aliases, and an existing directory
that does not traverse a symlink, junction, or reparse point. A warning, failed,
skipped, or interrupted result is never a root. In particular, a warning result's
`.failed` staging directory is partial recovery data, not a finalized output.
No successful output may collide with any non-null staging alias under host path
semantics. A directory containing ArcShuttle's `.arcshuttle-owned` marker is retained
staging and is rejected even if a result labels it as a successful output.

The default `--on-upstream-error fail` policy returns no roots if any result has a
non-success status or any result warning. `--on-upstream-error skip` may retain
validated finalized success roots, reports every omitted result and warning, and
requires a non-success chdmanpy exit even if later conversion succeeds. Structural,
summary, alias, path, or finalized-directory errors always reject the complete stream;
the skip policy does not weaken validation.

## Producer-exit limitation

ArcShuttle's process exit code is not present in schema-v2 result or summary records.
ArcShuttle can also use `--on-input-error skip`, return exit 1, and emit an otherwise
all-success stream that does not describe the input paths omitted during planning.
Consequently, chdmanpy cannot detect that producer-side condition from JSON Lines
alone. The normal ArcShuttle default fails input errors without a result stream. A
workflow that must prove a clean producer exit must capture ArcShuttle output and its
exit separately, check the exit, and only then pass the complete saved stream to
chdmanpy. Shell `pipefail` does not communicate the upstream exit code to the
chdmanpy process.

The captured public-shape fixture is
`tests/fixtures/arcshuttle-v0.3.2-success.jsonl`.
