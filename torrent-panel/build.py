#!/usr/bin/env python3
"""Build script: concatenate CSS and JS into dist/."""
from pathlib import Path

ROOT = Path(__file__).parent
STATIC = ROOT / "torrent_panel" / "static"
COMMON = ROOT / "common"
DIST = STATIC / "dist"

DIST.mkdir(parents=True, exist_ok=True)

# CSS
css_files = [
    COMMON / "css" / "index.css",
    STATIC / "app.css",
    STATIC / "console.css",
]
css_content = "\n".join(
    f.read_text(encoding="utf-8") for f in css_files if f.exists()
)

# CSS modules
css_module_dir = STATIC / "css"
if css_module_dir.exists():
    for module in sorted(css_module_dir.glob("*.css")):
        css_content += "\n" + module.read_text(encoding="utf-8")

(DIST / "app.min.css").write_text(css_content, encoding="utf-8")

# JS
js_files = [
    COMMON / "js" / "api.js",
    STATIC / "app.js",
    STATIC / "console.js",
]
js_content = "\n".join(
    f.read_text(encoding="utf-8") for f in js_files if f.exists()
)
(DIST / "app.min.js").write_text(js_content, encoding="utf-8")

print(f"Build complete: {DIST}/app.min.css + app.min.js")
