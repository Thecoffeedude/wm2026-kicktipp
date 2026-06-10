"""Render icon.svg to PNG icon set using Playwright."""
import os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

SVG = Path(__file__).parent.parent / "docs/icons/icon.svg"
OUT = SVG.parent

SIZES = [
    ("icon-192.png",          192,  False),
    ("icon-512.png",          512,  False),
    ("icon-512-maskable.png", 512,  True),   # maskable: full-bleed square bg
    ("apple-touch-icon.png",  180,  False),
]

svg_src = SVG.read_text()

def make_html(size: int, maskable: bool) -> str:
    radius = "0" if maskable else "22.3%"   # maskable = square (full bleed)
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  html,body{{width:{size}px;height:{size}px;overflow:hidden;background:transparent}}
  .wrap{{width:{size}px;height:{size}px;border-radius:{radius};overflow:hidden}}
  svg{{width:100%;height:100%;display:block}}
</style></head><body>
<div class="wrap">{svg_src}</div>
</body></html>"""

with sync_playwright() as p:
    browser = p.chromium.launch()
    for filename, size, maskable in SIZES:
        page = browser.new_page(viewport={"width": size, "height": size})
        page.set_content(make_html(size, maskable))
        page.wait_for_timeout(120)   # let gradients render
        dest = OUT / filename
        page.screenshot(path=str(dest), omit_background=False)
        print(f"  ✓  {filename}  ({size}×{size})")
    browser.close()

print("Done.")
