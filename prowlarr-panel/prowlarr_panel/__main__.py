import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "prowlarr_panel.main:app",
        host=os.getenv("PROWLARR_PANEL_HOST", "0.0.0.0"),
        port=int(os.getenv("PROWLARR_PANEL_PORT", "3120")),
        log_level=os.getenv("PROWLARR_PANEL_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
