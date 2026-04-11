"""klip-scanner — entrypoint (delegates to sync worker loop)."""
from __future__ import annotations

from klip_scanner.worker import main

if __name__ == "__main__":
    main()
