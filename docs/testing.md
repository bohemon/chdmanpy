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

The artifact smoke test checks that the sdist contains both READMEs, the
normative manuals, and the optional PowerShell helper. It then installs the
universal wheel with `--no-index --no-deps` and checks both entry points,
bundled presets, and installed CLI help without importing the source checkout.

`hatch run check` also validates local Markdown links and the aligned English
and Japanese command documentation.

A real CHDMAN installation is optional. When it is available on `PATH`, run the
explicit integration smoke test with:

```sh
hatch run test-real-chdman
```
