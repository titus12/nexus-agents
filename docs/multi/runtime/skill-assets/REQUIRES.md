# Runtime requirements

## Zhongshu citation and recovery correction (2026-10-06)

- Reused evidence keeps the authoritative source/quote from available_evidence.
  Equivalent workspace file path aliases can be canonicalized by transport;
  text, line coordinates and source symbol/line suffixes remain unchanged.
  New investigation sources need a separate update without source_evidence_id.
- Zhongshu's consecutive-failure brake separates reply, external and convergence
  failure classes. Same-class repetition and cumulative retry limits stay bounded.
  Technical retry or operator recovery never approves a PLAN_REVIEW document.
  Menxia retains its own recovery behavior and phase-bound result contract.

## Zhongshu contract correction (2026-10-05)

- Stable group IDs have no mandatory prefix: ASCII letter/digit first, followed
  by letters/digits/underscore/dot/hyphen. Preserve exact case and copy the ID
  into `# <group_id> 需求文档 [v<N>]`; keep title/group/member references consistent.
- Both item and projected group prerequisite graphs must be DAGs. Ignore
  within-group edges. Split upstream measurement and downstream summary if
  their common group makes other groups depend back on it; preserve true edges.
- A qualitative feasibility/optimization-space review is not a measured
  improvement promise. Only actual numeric/guaranteed outcomes require a
  measured source or 待实测. Do not invent measurements or mask claims with
  a feasibility word.
- Rejected output never becomes canonical review state. Repair against the
  accepted context and injected stage. Human feedback remains Solver-only,
  then another human review; only exact trimmed `通过` releases to Menxia.

- Read current prompt.txt, context.json and active-skill.md. Only the dispatch's
  phase-bound runtime skill applies. The injected Python result contract is the
  source of field names, types, actions, mode and identity coordinates.
- Return exactly one complete inline JSON object. No Markdown, fences, partial
  results, process comments, or invented mandatory fields. Copy envelope values
  from the dispatch. Use the contract's empty values for unused required fields.
- Do not use todo_write. Complete inherited pending todos before submitting.
- Reuse supplied evidence and limit investigation to the assigned product.
  A contract repair fixes the reported defect without a new source audit.
- Only an explicit platform output-overflow notice enables the current bundle's
  declared result-file fallback. Never use an arbitrary path or pointer format.
- Missing evidence and human decisions use the current contract's escalation
  channels. Agents do not self-approve gates or change workflow state.
