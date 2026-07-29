"""Compatibility entry point for the MH-Dowsample local server."""

from __future__ import annotations

if __name__ == "__main__":
    from server_engine import main

    main()
else:
    import sys
    import server_engine as _implementation

    sys.modules[__name__] = _implementation
