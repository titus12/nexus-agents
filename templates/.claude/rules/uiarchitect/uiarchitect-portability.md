# UIArchitect Portability

- UIArchitect AIConfig must not depend on a host project's business paths or modules.
- Host-specific paths must be configurable or isolated in wrappers or manifests.
- Do not require host-specific design documents, services, events, caches, or feature cards.
- Keep reusable plugin AIConfig source under a portable UIArchitect-owned path so it can move with the plugin.
