from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


ORCHESTRATOR_SCRIPT = "review_orchestrator_v2.py"
TASK_MARKER = "任务需求："
SCRIPT_TOKEN_RE = re.compile(r"(?i)review_orchestrator_v2(?:\.py)?$")


@dataclass(frozen=True)
class FeishuInstruction:
    """The safe result of parsing one Feishu message.

    `task_request` is the only field that may be passed to
    `review_orchestrator_v2.py --new`. The original message and command tokens
    are retained only for diagnostics and user-facing error messages.
    """

    raw_text: str
    task_request: str | None
    command_tokens: tuple[str, ...] = ()
    command_start: int | None = None
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        return bool(self.task_request and not self.error)


class FeishuInstructionParser:
    """Extensible parser for Feishu messages that start local tasks."""

    def parse(self, text: str) -> FeishuInstruction:
        raw_text = text or ""
        command = _find_first_orchestrator_command(raw_text)
        if command is not None:
            tokens, start = command
            task_request = _value_after_option(tokens, "--new")
            if not task_request:
                return FeishuInstruction(
                    raw_text=raw_text,
                    task_request=None,
                    command_tokens=tuple(tokens),
                    command_start=start,
                    error="检测到 review_orchestrator_v2.py，但缺少 --new 任务需求。",
                )
            return FeishuInstruction(
                raw_text=raw_text,
                task_request=task_request.strip(),
                command_tokens=tuple(tokens),
                command_start=start,
            )

        direct_request = _extract_explicit_task_request(raw_text)
        if direct_request:
            return FeishuInstruction(raw_text=raw_text, task_request=direct_request)

        return FeishuInstruction(
            raw_text=raw_text,
            task_request=None,
            error=(
                "未识别到有效任务需求。请提供 "
                "python ... review_orchestrator_v2.py --new \"任务需求\"，"
                "或使用“任务需求：<内容>”。"
            ),
        )


def parse_feishu_instruction(text: str) -> FeishuInstruction:
    """Parse the first supported task instruction in a Feishu message."""
    return FeishuInstructionParser().parse(text)


def extract_task_request(text: str) -> str:
    """Return only the business request, raising on malformed input.

    This function deliberately never executes or returns the launcher command.
    """

    result = parse_feishu_instruction(text)
    if not result.is_valid:
        raise ValueError(result.error or "无法提取任务需求。")
    return result.task_request or ""


def normalize_task_request(text: str) -> str:
    """Normalize a --new value without changing ordinary direct requests.

    A plain request remains byte-for-byte equivalent apart from surrounding
    whitespace. A value containing a supported launcher command is reduced to
    that command's --new value. Malformed launcher commands fail closed.
    """

    raw_text = text or ""
    command = _find_first_orchestrator_command(raw_text)
    if command is not None:
        return extract_task_request(raw_text)
    direct_request = _extract_explicit_task_request(raw_text)
    if direct_request:
        return direct_request
    return raw_text.strip()


def _find_first_orchestrator_command(text: str) -> tuple[list[str], int] | None:
    for match in re.finditer(r"(?i)(?<![\w])python(?:\.exe)?\s+", text):
        position = match.start()
        candidate = list(_tokenize_with_positions(text[position:]))
        script_index = next(
            (
                offset
                for offset, (candidate_token, _candidate_position) in enumerate(candidate)
                if SCRIPT_TOKEN_RE.search(candidate_token)
            ),
            None,
        )
        if script_index is not None:
            return [item[0] for item in candidate], position
    return None


def _value_after_option(tokens: Iterable[str], option: str) -> str | None:
    values = list(tokens)
    option_lower = option.lower()
    for index, token in enumerate(values):
        lower = token.lower()
        if lower == option_lower:
            if index + 1 < len(values):
                return values[index + 1]
            return None
        if lower.startswith(option_lower + "="):
            return token[len(option) + 1 :]
    return None


def _extract_explicit_task_request(text: str) -> str | None:
    marker_index = text.find(TASK_MARKER)
    if marker_index < 0:
        return None
    value = text[marker_index + len(TASK_MARKER) :]
    return value.strip() or None


def _tokenize_with_positions(text: str) -> Iterable[tuple[str, int]]:
    """Tokenize enough of Windows-style command text for --new extraction."""

    index = 0
    length = len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break

        start = index
        chars: list[str] = []
        quote: str | None = None
        while index < length:
            char = text[index]
            if quote:
                if char == "\\" and index + 1 < length and text[index + 1] == quote:
                    chars.append(quote)
                    index += 2
                    continue
                if char == quote:
                    quote = None
                    index += 1
                    continue
                chars.append(char)
                index += 1
                continue
            if char in {'"', "'"}:
                quote = char
                index += 1
                continue
            if char.isspace():
                break
            chars.append(char)
            index += 1

        if chars:
            yield "".join(chars), start
        while index < length and text[index].isspace():
            index += 1
