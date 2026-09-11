#!/usr/bin/env python3
"""Assemble <Name>.dc.html from shell-template.html + screens/<Name>.content.html.

Usage: python3 build.py Instances instances      -> Instances.dc.html with nav 'instances' active
       python3 build.py SignIn -                  -> no shell (content file is the whole artboard body)
The content file may start with a line  <!-- shell: none -->  to skip the shell entirely.
"""
import sys, re, pathlib

root = pathlib.Path(__file__).parent
name, nav = sys.argv[1], sys.argv[2]
template = sys.argv[3] if len(sys.argv) > 3 else "shell-template.html"   # e.g. shell-workspace-template.html
content = (root / "screens" / f"{name}.content.html").read_text()
shell = (root / template).read_text()

ACTIVE = 'style="height:34px;display:flex;align-items:center;gap:10px;padding:0 10px;border-radius:8px;font-size:13px;background:#cce5ff;color:#006194;font-weight:600;"'
INACTIVE = 'style="height:34px;display:flex;align-items:center;gap:10px;padding:0 10px;border-radius:8px;font-size:13px;color:#0b1c30;font-weight:500;"'

if content.lstrip().startswith("<!-- shell: none -->"):
    head, _, _ = shell.partition("<div style=\"width:1440px")
    out = head + content.split("-->", 1)[1].lstrip() + "\n</x-dc>\n</body>\n</html>\n"
else:
    if nav != "-":
        shell = shell.replace(f'data-nav="{nav}" {INACTIVE}', f'data-nav="{nav}" {ACTIVE}')
        assert ACTIVE in shell, f"nav '{nav}' not found"
    out = shell.replace("      <!-- MAIN CONTENT -->\n", content)

(root / f"{name}.dc.html").write_text(out)
print("wrote", f"{name}.dc.html", len(out), "bytes")
