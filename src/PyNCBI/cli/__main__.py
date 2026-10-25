"""Entry point for ``python -m PyNCBI.cli``.

``python -m PyNCBI.cli.main`` also works, but runpy warns there because the package
facade imports that module eagerly to expose ``main``; the module and the function
share a name, so the import cannot be made lazy without the attribute changing
meaning. This module is the warning-free form.
"""

from __future__ import annotations

import sys

from PyNCBI.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
