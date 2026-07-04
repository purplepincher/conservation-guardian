"""Allow ``python -m conservation_guardian`` to invoke the CLI."""

from .cli import main

raise SystemExit(main())
