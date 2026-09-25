# Contributing

1. Open an issue describing the behavior or improvement without private data.
2. Create a branch, make a focused change and add regression tests.
3. Install `requirements-dev.txt` and run `python -m pytest -q`.
4. For storage changes, run PostgreSQL tests against a disposable database; see [testing](docs/TESTING.md).
5. Submit a pull request describing the change, verification and limitations.

Do not commit credentials, production data or tests that call public services by default.
