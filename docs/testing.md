# Testing

The complete local quality gate is:

```sh
hatch run check
```

It runs Ruff linting, a Ruff format check, and the required pytest suite. The
suite uses the deterministic fake CHDMAN in `tests/fake_chdman.py`; it does not
download or require CHDMAN, MAME, ArcShuttle, 7-Zip, or network access.

Build both distributions and verify the wheel in a clean, temporary virtual
environment with:

```sh
hatch build
hatch run smoke-wheel
```

The wheel smoke test installs with `--no-index --no-deps` and checks both
`chdmanpy --version` and `python -m chdmanpy --version`.

A real CHDMAN installation is optional. When it is available on `PATH`, run the
explicit integration smoke test with:

```sh
hatch run test-real-chdman
```
