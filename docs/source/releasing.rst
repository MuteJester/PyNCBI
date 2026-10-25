Release validation and hosting
==============================

Release tags identify validated source commits. Publish a final release only after its
commit passes the required code, documentation, platform and packaging checks.

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

The publish job uses the existing ``PYPI_API_TOKEN`` GitHub Actions secret when present.
Only the publishing job receives that secret; it is never printed or stored in artifacts.
With no token configured, the same PyPA action uses Trusted Publishing (OIDC). Configure
a GitHub trusted publisher on the PyPI project with:

* Owner: ``MuteJester``
* Repository: ``PyNCBI``
* Workflow filename: ``python-publish.yml``
* Environment: ``pypi``

After configuring the trusted publisher, remove the legacy GitHub secret to switch to
OIDC and enable publishing attestations. The ``pypi`` GitHub environment must exist.
No token needs to be placed in source files or sent through chat.

Publication uses the wheel/source archive produced and tested earlier in the same run.
After upload, ``scripts/verify_pypi_release.py`` compares their SHA256 digests with PyPI,
attaches those same files and ``SHA256SUMS`` to the GitHub release, and installs the
published version without dependencies for an offline CLI smoke check. A publication
failure must be investigated before declaring success. If upload succeeded but a later
step failed, rerun failed jobs to reuse the original artifacts. Rebuilding can change
archive bytes; existing PyPI files are immutable and a digest mismatch is an error.

Release metadata
----------------

``src/PyNCBI/__init__.py`` is the package-version source. Setuptools reads it into the
wheel metadata. Before a release, set the matching ``CITATION.cff`` version and actual
``date-released``, and change the changelog's unreleased heading to the release version
and date. The workflow rejects a release tag that differs from the package version.
Use a prerelease package version and matching tag when publishing a release candidate.

Documentation hosting
---------------------

The canonical documentation is ``https://mutejester.github.io/PyNCBI/``. The CI workflow
builds Sphinx with warnings as errors, copies ``llms.txt`` and deploys the validated output
to GitHub Pages after the main-branch test matrix passes. Pages is configured to use
GitHub Actions. Verify the homepage, migration guide, agent setup guide and ``llms.txt``
after merging a release, before announcing it.

The legacy Read the Docs configuration remains available, but its account integration
needs separate repair before relying on that mirror. Local Sphinx builds do not repair
hosted builds or account settings.
