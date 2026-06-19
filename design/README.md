# Nexus Agents Design Prototype

This directory contains the static HTML prototype for Nexus Agents.

## Run
```powershell
cd D:\workspace\src\nexus-agents\design
.\serve.ps1
```

Open:
```text
http://127.0.0.1:8765/demo.html
```

HTTP is still the preferred preview mode. Directly opening `demo.html` also works through
`js/fragment-cache.js`, which embeds the page and overlay fragments for `file://` fallback.
Regenerate it after changing fragments:
```powershell
node ..\scripts\build_fragment_cache.mjs
```

## Structure
```text
demo.html          shell, sidebar, topbar, fragment mount points
shared.css         shared design tokens and component styles
pages/             page fragments, no html/head/body/script tags
overlays/          modal and drawer fragments
js/mock-data.js    prototype data
js/common.js       shared interactions
js/pages.js        page renderers
js/workflow-canvas.js
js/fragment-cache.js generated fallback fragments for file:// preview
js/nav.js          page and overlay loader
```

## Verification
```powershell
python ..\scripts\verify_design.py
```

The prototype mirrors the ResCenter design workflow: design first, implementation later.
