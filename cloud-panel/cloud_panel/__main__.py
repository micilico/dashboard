from __future__ import annotations

import os
import uvicorn

from .config import PUBLIC_PREFIX

if __name__ == "__main__":
    host = os.getenv("CLOUD_PANEL_HOST", "127.0.0.1")
    port = int(os.getenv("CLOUD_PANEL_PORT", "3130"))
    uvicorn.run(
        "cloud_panel.main:app",
        host=host,
        port=port,
        reload=False,
        log_level=os.getenv("CLOUD_PANEL_LOG_LEVEL", "info").lower(),
    )
