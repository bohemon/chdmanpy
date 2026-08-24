# Testing

[README](../README.md) · [Usage guide](usage.md)

The complete local quality gate is:

```sh
hatch run check
```

It runs Ruff linting, a Ruff format check, and the required pytest suite. The
suite uses the deterministic fake CHDMAN in `tests/fake_chdman.py`; it does not
download or require CHDMAN, MAME, ArcShuttle, 7-Zip, or network access.

Build both distributions, verify the source-distribution documentation set, and
verify the wheel in a clean, temporary virtual environment with:

```sh
hatch build
hatch run smoke-wheel
```

The release smoke test checks wheel/sdist metadata against an explicit content
allowlist and rejects repository caches, legacy preset examples, downloaded
binaries, and build leftovers. It verifies that the wheel-packaged READMEs and
manuals match their repository sources and that their internal links resolve.
It then installs the universal wheel with `--no-index --no-deps` outside the
checkout and exercises both entry points,
bundled presets, direct conversion, manifest execution, ArcShuttle-result
ingestion, historical `[options]` TOML, logs, representative failure, malformed
upstream input, process interruption, manifest-collision preflight, and
non-destructive existing-output handling through the deterministic fake CHDMAN.

`hatch run check` also validates local Markdown links and the aligned English
and Japanese command documentation.

A real CHDMAN installation is optional. When it is available on `PATH`, run the
explicit integration smoke test with:

```sh
hatch run test-real-chdman
```
