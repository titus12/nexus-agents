# Card answer preservation and recommendation implementation plan

Goal: prevent cross-page refresh from destroying unsent answers and let users explicitly select each question's recommendation.

Evidence: latest task 150f15 inbox 6 contains only page-2 q0_text="按你的推荐". Bridge and domain both committed exactly one answer. No evidence exists for delivery of page-1 answers. Existing refresh loops over all pages, so unsaved inputs may be cleared; client-side loss is a risk supported by code, not a verified reconstruction of user clicks.

Architecture: preserve all partially unanswered forms during automatic refresh; only refresh fully answered pages. Display per-page submission scope. Render a named recommendation selector per unanswered question; resolve it at the authenticated gateway against the immutable published descriptor, then commit exact text and existing option IDs. User text or selected options take precedence. Recommendations never count as answers without explicit selection. Existing gates missing recommendations resolve against matching current question revision. No automatic answers or new model runs.

- [x] Add card and gateway regression coverage for six answers submitted through two pages, recommendation-only submission, explicit answer precedence, and unsent-page preservation.
- [x] Implement recommendation helper/rendering, page descriptor recommendation binding, gateway interpretation, and safe clarification refresh.
- [x] Run guarded tests via cmd/run_tests.py; reload four idle task runners, update latest question cards with preserved answers, and read platform card content. Gateway process requires no reload because interpretation runs in the task runner.

Acceptance: 25 tests passed. Actual latest page 1 contains five explicit recommendation selectors, page 2 still displays the recorded sixth answer; both messages remain undeleted. Readback evidence: runs/maintenance/question-recommendation-acceptance.json. Latest task remains partial at sequence 4 with one committed answer. The five missing submissions cannot be reconstructed from server data and were not fabricated.
