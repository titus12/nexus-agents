# Evidence protocol alignment, 2026-10-07

The deployed Multica content was read through the official CLI, including the three
review agents, their attached Zhongshu skills, all REQUIRES.md dependencies and the
generic orchestrator/document protocols. Before/after JSON and exact readback hashes
are under `runs/maintenance/`; `requirements-protocol-sync-report.json` records results.

The three deployed Zhongshu skills and agent instructions still contained retired
extraction/evidence-packet/freeze behavior, STATUS locks and plain-text approval.
Their dependencies repeated those rules. Local locked role snapshots additionally
described direct result-file submission while the injected transport required normal
inline JSON. The fact schema exposed source_fingerprint as an unconstrained string,
although runtime compared it to raw file SHA-256. Invalid quotes were terminal failures
and only the first defect was reported. Relative paths depended on the process cwd.

The deployed skills, dependencies and three instructions now follow the locked
requirements-v1 role/contract. Models and skill assignments were retained. Runtime
computes fingerprints; incoming values may only be omitted or empty. All rejected
fact indexes/IDs/sources are reported together, with at most two format repairs on
the same logical dispatch. CRLF/LF equivalence is accepted, without altering text,
indentation or raw-byte hashes. Approved inputs and false evidence remain protected.

The unchanged failed result was replayed offline after removing the model-authored
fingerprint labels. Exactly two nonmatching quotes remain; both are reported for
bounded repair. No live model rerun or external message was triggered by validation.

Start new tasks from the Nexus directory and supply an explicit target repository:

```powershell
Set-Location -LiteralPath 'D:\workspace\src\nexus-agents'
python -X utf8 cmd/review_orchestrator_v2.py `
  --project '1e41be52-4460-4fba-9360-0c60cfc18c92' `
  --project-root 'D:\workspace\src\rtc-integration-unity-client' `
  --project-type unity --task-type review `
  --runs-root 'D:\workspace\src\nexus-agents\runs' `
  --poll-interval 15 --timeout 900 --allow-duplicate `
  --communication feishu --decision-actor-id 'ou_REPLACE_WITH_REAL_OPEN_ID' `
  --new '审查 Packages\com.centurygame.rtc.sdk\Runtime 中的 RTC SDK 网络层 TLV，目标是性能和 GC 分配优化。'
```

Replace the owner placeholder before running. Existing tasks retain immutable owners,
root and locked bundles; source/skill edits do not rewrite their history. CLI recovery
still requires verified changed prerequisites. The Nexus service was not restarted.
