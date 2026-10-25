Release validation and hosting
==============================

The repository prepares version 1.0.0. Do not publish a final release until the exact
release commit passes its required checks.

Local validation
----------------

Follow the checks in ``CONTRIBUTING.md``. Build into a clean output directory, then run
``python scripts/check_distribution.py DIST --smoke``. This verifies the wheel/source
contents and installs the wheel with ``--no-deps --no-index`` into a fresh environment.
It rejects any declared third-party runtime requirement. The CLI, schemas, offline status and MCP manifest are exercised there.
CI additionally unpacks the source archive and runs its offline tests.

The matrix includes Python 3.10–3.15 on Linux and Python 3.12 on Windows/macOS. Python
3.10 is retained for legacy users even though upstream support has ended. A configured
matrix is not proof of compatibility: review the actual jobs before releasing. Future
removal of a compatibility target requires an explicit release note.

Live NCBI checks run weekly, on manual dispatch and on published release events. Publishing
is gated on those checks for release events. They use ``NCBI_API_KEY`` from a GitHub Actions
secret when configured; the public API can also be used without a key. Failures are not
silently rerun without timeouts. Review endpoint and rate-limit failures before retrying.

PyPI account setup
------------------

The publish job uses Trusted Publishing (OIDC), not a stored API token. On the existing
PyPI project, configure a GitHub trusted publisher with:

* Owner: ``MuteJester``
* Repository: ``PyNCBI``
* Workflow filename: ``python-publish.yml``
* Environment: ``pypi``

Create the ``pypi`` GitHub Actions environment and configure any desired release protections.
This account setup cannot be established by editing the workflow alone. No PyPI API token
needs to be placed in source files or sent through chat.

Release metadata
----------------

``src/PyNCBI/__init__.py`` is the package-version source. Setuptools reads it into the
wheel metadata. Before a release, set the matching ``CITATION.cff`` version and actual
``date-released``, and change the changelog's unreleased heading to the release version
and date. The workflow rejects a release tag that differs from the package version.
Use a prerelease package version and matching tag when publishing a release candidate.

Documentation hosting
---------------------

In the Read the Docs project, verify that the repository and default branch point to
this repository, that ``.readthedocs.yaml`` is used, and that the webhook/build integration
is connected. Trigger a build of the intended commit. The default ``latest`` build and
a versioned release build should both render the new guides and API reference.

Check the homepage and ``/en/latest/`` after rebuilding. A local Sphinx build does not
update hosted documentation. These hosting settings require access to the Read the Docs
account; they cannot be repaired by changing local RST files alone.
