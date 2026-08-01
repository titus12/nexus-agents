<!-- OPENWIKI:START -->

## OpenWiki

This repository uses OpenWiki for recurring code documentation. Start with `openwiki/quickstart.md`, then follow its links to architecture, workflows, domain concepts, operations, integrations, testing guidance, and source maps.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->

## Local verification

- When running automated tests, smoke checks, or other validation for this repository, use a separate port from the active Nexus service. Keep the current conversation's Nexus instance available on its configured port (normally `8766`); use another port such as `18766` for the validation instance instead of stopping or rebinding the active service.
