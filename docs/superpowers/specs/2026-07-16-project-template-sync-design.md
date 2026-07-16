# Project Template Synchronization Design

## Goal

Give every imported project a manual action that copies the current Nexus Agents
template set into that project. The action refreshes project AI configuration after
`templates/` changes, without touching project knowledge content.

## User interface

On each project Overview page, add a `同步 Nexus 模板` button immediately to the
left of `重新扫描`.

Clicking the button starts one project-level synchronization request. The existing
single-item synchronization and preview workflows remain unchanged.

After a successful request, the UI replaces the current project's project record
and copied-config list with the result returned by the server, then displays:

```text
模板同步完成：覆盖 <overwritten> 个，新增 <created> 个，跳过 <skipped> 个。
```

The button is temporarily disabled while the request is in flight. Errors use the
existing request-error presentation and leave the current UI data intact.

## Synchronization rules

The source is the Nexus Agents repository `templates/` directory. For every
ordinary file found below it:

1. Compute its relative path below `templates/`.
2. Normalize the relative path and reject entries that would escape the target
   project root.
3. If the relative path is `KnowledgeBase/project` or is below
   `KnowledgeBase/project/`, skip it unconditionally.
4. Otherwise, map the relative path to the imported target project's root.
5. Create any missing parent directories and copy the template file bytes to the
   target path.
   - If the target file already exists, count it as `overwritten`.
   - If it does not exist, count it as `created`.

The operation does not delete target files that are absent from `templates/`, and
does not synchronize unrelated files outside the template tree.

The exclusion is applied from the template-relative path, before a destination
file is opened or created. Therefore `KnowledgeBase/project` is neither created
nor overwritten, including when it already exists in the target project.

## Backend design

Add a store operation that:

1. Resolves the requested project and its local root using the same rules as
   `RescanProject`.
2. Walks the repository template root.
3. Copies eligible files using the synchronization rules above while collecting
   `overwritten`, `created`, and `skipped` counters.
4. Calls the existing `RescanProject` path after the copy completes.
5. Returns the rescanned project and config copies with the three counters.

Expose the operation as a project-scoped POST endpoint adjacent to the existing
`/api/projects/{id}/rescan` endpoint. The endpoint returns 404 for an unknown
project and returns an error without a successful response if the template or
target project path cannot be accessed.

The response type extends the existing rescan response shape with a synchronization
summary, so the frontend can update its project state with the same code path it
uses for rescan results.

## Safety and error handling

- Template-relative paths must not resolve outside the destination project.
- The synchronization process copies only regular files; directories are created
  only as parents of eligible files.
- A failed copy stops the operation and returns the underlying error. The result
  may include files copied before the failure; no rollback is attempted.
- No `KnowledgeBase/project` destination path is read, created, or overwritten.
- The protected Codex global configuration files are not in the project template
  tree and are outside this feature's write scope.

## Test plan

Add catalog tests using temporary template and project roots to prove:

1. A matching destination file is overwritten with template content.
2. A missing destination file and its parent directories are created.
3. Destination-only files are preserved.
4. `KnowledgeBase/project` files are skipped whether or not they already exist.
5. The returned project and copies are populated from the post-sync rescan.
6. Missing projects and inaccessible roots produce errors or not-found behavior
   consistent with `RescanProject`.

Add HTTP handler coverage for the new POST route and frontend type/API coverage
where this repository's existing tests cover those surfaces.
