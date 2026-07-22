#!/usr/bin/env python3
"""Build script: concatenate CSS and JS into dist/."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))
from common import resolve_css_imports

STATIC = ROOT / "cloud_panel" / "static"
COMMON = ROOT / "common" if (ROOT / "common").exists() else ROOT.parent / "common"
DIST = STATIC / "dist"

DIST.mkdir(parents=True, exist_ok=True)

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

(DIST / "app.min.css").write_text(css_content, encoding="utf-8")

# JS
js_files = [
    COMMON / "js" / "api.js",
    STATIC / "app.js",
]
js_content = "\n".join(
    f.read_text(encoding="utf-8") for f in js_files if f.exists()
)
(DIST / "app.min.js").write_text(js_content, encoding="utf-8")

print(f"Build complete: {DIST}/app.min.css + app.min.js")
