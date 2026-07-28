#!/usr/bin/env python3
"""Build script: concatenate CSS and JS into dist/."""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))
from common import resolve_css_imports  # noqa: E402

STATIC = ROOT / "torrent_panel" / "static"
COMMON = ROOT / "common" if (ROOT / "common").exists() else ROOT.parent / "common"
DIST = STATIC / "dist"

DIST.mkdir(parents=True, exist_ok=True)

# Self-hosted fonts must live next to the generated bundle so relative URLs
# keep working behind any configured public prefix.
font_source = COMMON / "fonts" / "Inter-Variable.woff2"
font_destination = STATIC / "fonts" / font_source.name
font_destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(font_source, font_destination)

# CSS — keep the main application and system console rules isolated. app.css
# already resolves its own module imports, so adding css/*.css again would
# duplicate rules and change their cascade order.
common_css = resolve_css_imports(COMMON / "css" / "index.css")
app_css_content = common_css + "\n" + resolve_css_imports(STATIC / "app.css")
console_css_content = common_css + "\n" + resolve_css_imports(STATIC / "console.css")

(DIST / "app.min.css").write_text(app_css_content.rstrip() + "\n", encoding="utf-8")
(DIST / "console.min.css").write_text(console_css_content.rstrip() + "\n", encoding="utf-8")

# JS - app.min.js (pour index.html)
js_files_app = [
    COMMON / "js" / "api.js",
    COMMON / "js" / "dom.js",
    COMMON / "js" / "navigation.js",
    COMMON / "js" / "focus-trap.js",
    STATIC / "app.js",
]
js_content_app = "\n".join(
    f.read_text(encoding="utf-8") for f in js_files_app if f.exists()
)
(DIST / "app.min.js").write_text(js_content_app, encoding="utf-8")

# JS - console.min.js (pour les pages console: activity, storage, media, health)
js_files_console = [
    COMMON / "js" / "api.js",
    COMMON / "js" / "dom.js",
    COMMON / "js" / "navigation.js",
    COMMON / "js" / "focus-trap.js",
    STATIC / "console.js",
]
js_content_console = "\n".join(
    f.read_text(encoding="utf-8") for f in js_files_console if f.exists()
)
(DIST / "console.min.js").write_text(js_content_console, encoding="utf-8")

print(
    f"Build complete: {DIST}/app.min.css + console.min.css "
    "+ app.min.js + console.min.js"
)
