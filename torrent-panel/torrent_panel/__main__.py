import os

import uvicorn


def main() -> None:
    host = os.getenv("TORRENT_PANEL_HOST", "127.0.0.1")
    port = int(os.getenv("TORRENT_PANEL_PORT", "3110"))
    uvicorn.run("torrent_panel.main:app", host=host, port=port, proxy_headers=True)


if __name__ == "__main__":
    main()
