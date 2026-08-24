# chdmanpy

[Japanese / 日本語](https://github.com/bohemon/chdmanpy/blob/main/README.ja.md)

chdmanpy is a pipeline-friendly command-line frontend for CHDMAN. It plans and
runs bounded parallel conversions while keeping machine-readable JSON Lines on
stdout. Archive extraction belongs to
[ArcShuttle](https://github.com/bohemon/ArcShuttle): chdmanpy neither extracts
ZIP files nor invokes ArcShuttle.

## Requirements

- Windows or Linux with Python 3.11 or later
- `chdman`, installed separately and available on `PATH`, through `--chdman`,
  or through configuration
- ArcShuttle only when archive extraction is needed

## Installation

Install a published release in an isolated environment:

```console
pipx install chdmanpy
```

From a source checkout, use `pipx install .`. Installing chdmanpy does not
install CHDMAN or ArcShuttle.

## Quick start

Convert a directory directly with the bundled PlayStation 2 preset:

```console
chdmanpy convert ./input --output-dir ./chd --preset ps2 >results.jsonl
```

For archives, connect ArcShuttle's schema-v2 result stream explicitly:

```sh
arcshuttle extract --output-dir ./extracted game.zip |
  chdmanpy convert --arcshuttle-results - --output-dir ./chd --preset ps2 \
  >results.jsonl
```

Diagnostics remain visible on stderr. See the usage manual before using a direct
pipeline when the ArcShuttle process exit must also be verified.

## Documentation

- [Usage and migration guide](https://github.com/bohemon/chdmanpy/blob/main/docs/usage.md)
  ([日本語](https://github.com/bohemon/chdmanpy/blob/main/docs/usage.ja.md))
- [chdmanpy JSON Lines schema v1](https://github.com/bohemon/chdmanpy/blob/main/docs/schema-v1.md)
- [ArcShuttle schema-v2 ingestion](https://github.com/bohemon/chdmanpy/blob/main/docs/arcshuttle-schema-v2.md)
  ([日本語](https://github.com/bohemon/chdmanpy/blob/main/docs/arcshuttle-schema-v2.ja.md))
- [Testing](https://github.com/bohemon/chdmanpy/blob/main/docs/testing.md)
