#!/usr/bin/env python3
"""Build script: concatenate CSS and JS into dist/."""
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))
from common import resolve_css_imports
from common.shell import render_shell_files

STATIC = ROOT / "cloud_panel" / "static"
COMMON = ROOT / "common" if (ROOT / "common").exists() else ROOT.parent / "common"
DIST = STATIC / "dist"

DIST.mkdir(parents=True, exist_ok=True)

render_shell_files(STATIC, {"index.html": "cloud"})

# Self-hosted fonts must live next to the generated bundle so relative URLs
# keep working behind any configured public prefix.
font_source = COMMON / "fonts" / "Inter-Variable.woff2"
font_destination = STATIC / "fonts" / font_source.name
font_destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(font_source, font_destination)

# CSS
css_content = resolve_css_imports(COMMON / "css" / "index.css")

app_css = STATIC / "app.css"
if app_css.exists():
    css_content += "\n" + resolve_css_imports(app_css)

# CSS modules
css_module_dir = STATIC / "css"
if css_module_dir.exists():
    for module in sorted(css_module_dir.glob("*.css")):
        css_content += "\n" + resolve_css_imports(module)

# JS
js_files = [
    COMMON / "js" / "api.js",
    COMMON / "js" / "dom.js",
    COMMON / "js" / "live.js",
    COMMON / "js" / "navigation.js",
    COMMON / "js" / "focus-trap.js",
    STATIC / "app.js",
]
js_content = "\n".join(
    f.read_text(encoding="utf-8") for f in js_files if f.exists()
)

# Hash source bundles and font before writing versioned references. Runtime
# cache headers only become immutable when the query matches this token.
asset_digest = hashlib.sha256()
asset_digest.update(css_content.encode("utf-8"))
asset_digest.update(js_content.encode("utf-8"))
asset_digest.update(font_destination.read_bytes())
asset_version = asset_digest.hexdigest()[:12]
css_content = re.sub(
    r"Inter-Variable\.woff2(?:\?v=[^)]+)?",
    f"Inter-Variable.woff2?v={asset_version}",
    css_content,
)

(DIST / "app.min.css").write_text(css_content, encoding="utf-8")
(DIST / "app.min.js").write_text(js_content, encoding="utf-8")
(DIST / "asset-version.txt").write_text(asset_version + "\n", encoding="ascii")
manifest_files = {
    "dist/app.min.css": hashlib.sha256((DIST / "app.min.css").read_bytes()).hexdigest(),
    "dist/app.min.js": hashlib.sha256((DIST / "app.min.js").read_bytes()).hexdigest(),
    "fonts/Inter-Variable.woff2": hashlib.sha256(font_destination.read_bytes()).hexdigest(),
}
(DIST / "asset-version.json").write_text(
    json.dumps({"version": asset_version, "files": manifest_files}, separators=(",", ":")),
    encoding="utf-8",
)

index_path = STATIC / "index.html"
index_html = index_path.read_text(encoding="utf-8")
index_html = re.sub(
    r"(config\.js|app\.min\.css|app\.min\.js)\?v=[^\"']+",
    lambda match: f"{match.group(1)}?v={asset_version}",
    index_html,
)
index_path.write_text(index_html, encoding="utf-8")

print(f"Build complete: {DIST}/app.min.css + app.min.js")
