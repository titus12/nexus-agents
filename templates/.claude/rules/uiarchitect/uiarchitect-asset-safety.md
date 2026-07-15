# UIArchitect Asset Safety

- Never delete `.meta` files.
- Treat Prefab, Scene, generated asset, imported sprite, atlas, and serialized reference changes as high risk.
- Avoid broad asset regeneration unless explicitly required.
- Verify serialized references and GUID-sensitive files after importer behavior changes.
