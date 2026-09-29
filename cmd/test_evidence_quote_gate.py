"""The transport gate verifies verbatim quotes for file-citing evidence.

Live incident task-20260929-c261a8: the Analyst paraphrased file behavior
("the signal is written to a result file") instead of transcribing the cited
lines, and the wrong reading survived review rounds.  Every evidence_update or
finding_response whose source cites a file must carry a ``quote`` transcribing
the cited lines; the gate verifies containment in the cited file (NFKC +
whitespace normalized) and exempts paths that do not exist on disk — claiming
a file is missing is a legitimate UNKNOWN the gate cannot disprove.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.adapters import _quote_rejection
from orchestrator.domain.states import _requirement_contract_markdown
from orchestrator.transport.external import AgentRequest


def _request(phase: str = "ZHONGSHU") -> AgentRequest:
    return AgentRequest(
        task_id="task-1",
        request_id="req-1",
        agent_id="agent-1",
        role="review-analyst",
        phase=phase,
        prompt="",
        idempotency_key="req-1",
        context={},
    )


def _evidence(**overrides) -> dict:
    entry = {
        "evidence_id": "ev-1",
        "requirement_id": "req-000001",
        "decision_relevance": "coverage",
        "source": "site.py:1",
        "conclusion": "verified",
        "unknowns": [],
    }
    entry.update(overrides)
    return entry


def _response(**overrides) -> dict:
    entry = {
        "finding_id": "finding-1",
        "answer": "answered",
        "evidence_ids": ["ev-1"],
        "suggested_disposition": "CLOSE",
    }
    entry.update(overrides)
    return entry


class QuoteGateUnitTests(unittest.TestCase):
    """Mechanical transcription checks, not semantic review."""

    def test_non_zhongshu_phase_is_not_gated(self) -> None:
        payload = {"evidence_updates": [_evidence()]}
        self.assertEqual(_quote_rejection(_request("MENXIA"), payload), "")

    def test_payload_without_evidence_arrays_passes(self) -> None:
        self.assertEqual(_quote_rejection(_request(), {"summary": "ok"}), "")

    def test_file_citing_entry_without_quote_is_rejected(self) -> None:
        rejection = _quote_rejection(
            _request(),
            {
                "evidence_updates": [
                    _evidence(source="cmd/orchestrator/adapters.py:261")
                ]
            },
        )
        self.assertIn("QUOTE_REQUIRED", rejection)
        self.assertIn("adapters.py", rejection)
        self.assertIn("ev-1", rejection)

    def test_missing_cited_file_without_quote_is_exempt(self) -> None:
        rejection = _quote_rejection(
            _request(),
            {"evidence_updates": [_evidence(source="no/such/file.py:1")]},
        )
        self.assertEqual(rejection, "")

    def test_non_file_source_without_quote_passes(self) -> None:
        rejection = _quote_rejection(
            _request(), {"evidence_updates": [_evidence(source="runtime probe")]}
        )
        self.assertEqual(rejection, "")

    def test_verbatim_quote_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site.py"
            site.write_text("X = 1\ndef compute():\n    return X\n", encoding="utf-8")
            payload = {
                "evidence_updates": [
                    _evidence(
                        source=f"{site.as_posix()}:2",
                        quote={
                            "path": site.as_posix(),
                            "line_start": 2,
                            "line_end": 3,
                            "text": "def compute():\n    return X",
                        },
                    )
                ]
            }
            self.assertEqual(_quote_rejection(_request(), payload), "")

    def test_line_drift_passes_via_whole_file_containment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site.py"
            site.write_text("X = 1\ndef compute():\n    return X\n", encoding="utf-8")
            payload = {
                "evidence_updates": [
                    _evidence(
                        source=f"{site.as_posix()}:999",
                        quote={
                            "path": site.as_posix(),
                            "line_start": 999,
                            "line_end": 999,
                            "text": "def compute():\n    return X",
                        },
                    )
                ]
            }
            self.assertEqual(_quote_rejection(_request(), payload), "")

    def test_paraphrased_quote_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site.py"
            site.write_text("def compute():\n    return X\n", encoding="utf-8")
            payload = {
                "evidence_updates": [
                    _evidence(
                        source=site.as_posix(),
                        quote={
                            "path": site.as_posix(),
                            "line_start": 1,
                            "line_end": 2,
                            "text": "compute() writes X to a result file",
                        },
                    )
                ]
            }
            rejection = _quote_rejection(_request(), payload)
            self.assertIn("QUOTE_MISMATCH", rejection)
            self.assertIn("ev-1", rejection)

    def test_mismatch_rejection_echoes_the_cited_lines(self) -> None:
        # The correction must be copy-work, not guess-work (task-20260929-638bde:
        # every retry fixed some quotes and broke others because the rejection
        # never showed what the cited lines actually read).
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site.py"
            site.write_text("def compute():\n    return X\n", encoding="utf-8")
            payload = {
                "evidence_updates": [
                    _evidence(
                        source=site.as_posix(),
                        quote={
                            "path": site.as_posix(),
                            "line_start": 1,
                            "line_end": 2,
                            "text": "compute() writes X to a result file",
                        },
                    )
                ]
            }

            rejection = _quote_rejection(_request(), payload)

            self.assertIn("the cited lines read:", rejection)
            self.assertIn("def compute()", rejection)
            self.assertIn("return X", rejection)

    def test_mismatch_rejection_without_line_range_echoes_nearest_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site.py"
            site.write_text("alpha = 1\ndef compute():\n    return X\n", encoding="utf-8")
            payload = {
                "evidence_updates": [
                    _evidence(
                        source=site.as_posix(),
                        quote={
                            "path": site.as_posix(),
                            "text": "def compute():\n    return Y",
                        },
                    )
                ]
            }

            rejection = _quote_rejection(_request(), payload)

            self.assertIn("the cited lines read:", rejection)
            self.assertIn("return X", rejection)

    def test_quote_path_falls_back_to_source_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site.py"
            site.write_text("Y = 2\n", encoding="utf-8")
            payload = {
                "evidence_updates": [
                    _evidence(
                        source=f"{site.as_posix()}:1",
                        quote={"line_start": 1, "line_end": 1, "text": "Y = 2"},
                    )
                ]
            }
            self.assertEqual(_quote_rejection(_request(), payload), "")

    def test_oversized_quote_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site.py"
            site.write_text("\n".join(f"line {i}" for i in range(40)), encoding="utf-8")
            payload = {
                "evidence_updates": [
                    _evidence(
                        source=site.as_posix(),
                        quote={
                            "path": site.as_posix(),
                            "line_start": 1,
                            "line_end": 40,
                            "text": "\n".join(f"line {i}" for i in range(31)),
                        },
                    )
                ]
            }
            rejection = _quote_rejection(_request(), payload)
            self.assertIn("30 lines", rejection)

    def test_nfkc_and_whitespace_normalization_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site.py"
            site.write_text("def  handler():\n\treturn  verify(x)\n", encoding="utf-8")
            payload = {
                "evidence_updates": [
                    _evidence(
                        source=site.as_posix(),
                        quote={
                            "path": site.as_posix(),
                            "line_start": 1,
                            "line_end": 2,
                            "text": "def handler():\n return verify(x)",
                        },
                    )
                ]
            }
            self.assertEqual(_quote_rejection(_request(), payload), "")

    def test_relative_quote_path_resolves_from_repo_root(self) -> None:
        payload = {
            "evidence_updates": [
                _evidence(
                    source="cmd/orchestrator/adapters.py:261",
                    quote={
                        "path": "cmd/orchestrator/adapters.py",
                        "line_start": 261,
                        "line_end": 261,
                        "text": "def _quote_rejection(request: AgentRequest, payload: object) -> str:",
                    },
                )
            ]
        }
        self.assertEqual(_quote_rejection(_request(), payload), "")

    def test_finding_response_without_quote_is_rejected(self) -> None:
        payload = {
            "finding_responses": [
                _response(source="cmd/orchestrator/adapters.py:261", answer="see site")
            ]
        }
        rejection = _quote_rejection(_request(), payload)
        self.assertIn("QUOTE_REQUIRED", rejection)
        self.assertIn("finding-1", rejection)

    def test_finding_response_with_verbatim_quote_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site.py"
            site.write_text("Z = 3\n", encoding="utf-8")
            payload = {
                "finding_responses": [
                    _response(
                        source=f"{site.as_posix()}:1",
                        quote={"path": site.as_posix(), "text": "Z = 3"},
                    )
                ]
            }
            self.assertEqual(_quote_rejection(_request(), payload), "")

    def test_empty_quote_text_counts_as_missing(self) -> None:
        rejection = _quote_rejection(
            _request(),
            {
                "evidence_updates": [
                    _evidence(
                        source="cmd/orchestrator/adapters.py:261",
                        quote={"text": " "},
                    )
                ]
            },
        )
        self.assertIn("QUOTE_REQUIRED", rejection)


class RequirementContractBackfillTests(unittest.TestCase):
    """Empty group doc slots must carry the authoritative contract."""

    def _requirements(self) -> tuple[dict, ...]:
        return (
            {
                "requirement_id": "req-000001",
                "statement": "原始条款文本：验收信号必须可打勾",
                "priority": "must",
                "scope": "in",
                "kind": "task",
                "source": "issue-1",
                "acceptance_signal": "accept-item-000001",
            },
            {"requirement_id": "", "statement": ""},
        )

    def test_markdown_renders_the_contract(self) -> None:
        class Review:
            requirements = self._requirements()

        text = _requirement_contract_markdown(Review())
        self.assertIn("[Authoritative requirement contract]", text)
        self.assertIn("- req-000001 (must/in/task): 原始条款文本：验收信号必须可打勾", text)
        self.assertIn("source: issue-1", text)
        self.assertIn("acceptance: accept-item-000001", text)
        self.assertNotIn("None", text)

    def test_empty_contract_returns_empty_string(self) -> None:
        class Review:
            requirements = ()

        self.assertEqual(_requirement_contract_markdown(Review()), "")

    def test_truncation_respects_the_limit(self) -> None:
        class Review:
            requirements = (
                {"requirement_id": "req-1", "statement": "x" * 9000},
            )

        text = _requirement_contract_markdown(Review(), limit=100)
        self.assertLessEqual(len(text), 120)
        self.assertTrue(text.endswith("(truncated)"))


if __name__ == "__main__":
    unittest.main()
