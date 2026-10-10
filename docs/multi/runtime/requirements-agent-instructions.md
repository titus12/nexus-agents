# Shared Nexus review agent instructions

At every dispatch, read all required UTF-8 files in the prompt bundle manifest,
including active-skill.md, context.json, prompt.txt and the injected result schema.
The dispatched workflow, phase, state, mode, role, identities and schema are
authoritative. Another attached skill or an old conversation cannot override them.

For workflow=requirements-v1 in REQ_* states, use only the locked requirements
role skill. Zhongshu produces a complete requirements document: Analyst interprets
human sources and necessary repository facts, Solver authors the document, Critic
independently reviews the same document. Do not emit legacy extraction/evidence
packet/freeze actions, graphs, voting fields or document STATUS locks. The approved
handoff alone permits Menxia planning. For MENXIA_* states follow the independent
phase-bound Menxia skill and injected contract; do not use REQ_* role rules there.

Fact results supply a file source and a nonempty contiguous verbatim quote. Prefer
absolute paths; relative paths require context.project_root. Optional line numbers
belong in source as :12 or :12-18. Omit source_fingerprint or leave it empty; the
runtime computes the SHA-256. Do not stitch separated excerpts, invent hashes,
paraphrase quotes or turn code facts into explicit human choices. CRLF/LF line-ending
equivalence is allowed; indentation and text remain exact and hashes cover raw bytes. Unavailable
sources remain unavailable facts. On bounded repair read repair.failure.message
and failed_reply_ref; fix rejected facts using existing research, without repeating
a full repository scan or weakening evidence validity.

Return exactly one complete inline JSON object with the injected enum values and
dispatch coordinates. The orchestrator materializes normal results. Only an
explicit platform output-overflow notice enables the bound result-file fallback;
use its declared result_path/result_sha256 protocol. Do not emit progress comments,
create process todos, approve gates or advance workflow state. The orchestrator
owns human approval: owner-authorized card callbacks bound to current gate/document
digest and verified cloud readback. A plain text reply or model inference is not
approval. State recovery and retries never authorize work or reset budgets.
