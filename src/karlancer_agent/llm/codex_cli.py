"""Structured Codex CLI adapter backed by the user's ChatGPT subscription."""

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from pydantic import BaseModel

from karlancer_agent.llm.base import (
    ProviderAuthenticationError,
    ProviderQuotaError,
    StructuredLLMProvider,
    StructuredModel,
)


@dataclass(frozen=True, slots=True)
class CodexProcessResult:
    """Sanitized result from one non-interactive Codex process."""

    returncode: int
    output: str
    stderr: str


class CodexRunner(Protocol):
    async def run(
        self,
        *,
        prompt: str,
        schema: dict[str, object],
        model: str,
        executable: str,
        reasoning_effort: str,
        timeout_seconds: int,
    ) -> CodexProcessResult: ...


class SubprocessCodexRunner:
    """Run Codex in an empty temporary workspace with no writable project files."""

    async def run(
        self,
        *,
        prompt: str,
        schema: dict[str, object],
        model: str,
        executable: str,
        reasoning_effort: str,
        timeout_seconds: int,
    ) -> CodexProcessResult:
        with TemporaryDirectory(prefix="karlancer-codex-") as temporary:
            workspace = Path(temporary)
            schema_path = workspace / "schema.json"
            output_path = workspace / "output.json"
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False), encoding="utf-8"
            )
            command = build_codex_command(
                executable=executable,
                model=model,
                reasoning_effort=reasoning_effort,
                schema_path=str(schema_path),
                output_path=str(output_path),
                working_directory=str(workspace),
            )
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode("utf-8")),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                return CodexProcessResult(
                    returncode=124,
                    output="",
                    stderr="Codex execution timed out",
                )
            output = (
                output_path.read_text(encoding="utf-8")
                if output_path.exists()
                else stdout.decode("utf-8", errors="replace")
            )
            return CodexProcessResult(
                returncode=process.returncode or 0,
                output=output,
                stderr=stderr.decode("utf-8", errors="replace"),
            )


def build_codex_command(
    *,
    executable: str,
    model: str,
    reasoning_effort: str,
    schema_path: str,
    output_path: str,
    working_directory: str,
) -> tuple[str, ...]:
    """Build the only permitted non-interactive invocation."""

    return (
        executable,
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--cd",
        working_directory,
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--output-schema",
        schema_path,
        "--output-last-message",
        output_path,
        "-",
    )


class CodexCLIProvider(StructuredLLMProvider):
    """Generate validated JSON through ChatGPT-authenticated Codex CLI."""

    def __init__(
        self,
        *,
        model: str,
        executable: str = "codex",
        reasoning_effort: str = "low",
        timeout_seconds: int = 120,
        runner: CodexRunner | None = None,
    ) -> None:
        super().__init__(model=model)
        self._executable = executable
        self._reasoning_effort = reasoning_effort
        self._timeout_seconds = timeout_seconds
        self._runner = runner or SubprocessCodexRunner()

    async def _request(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        schema: type[StructuredModel],
    ) -> str:
        prompt = (
            "Do not use tools. Do not inspect files or the environment. "
            "Follow the supplied messages and return only JSON matching the output schema.\n"
            + json.dumps({"messages": list(messages)}, ensure_ascii=False)
        )
        result = await self._runner.run(
            prompt=prompt,
            schema=_json_schema(schema),
            model=self.model,
            executable=self._executable,
            reasoning_effort=self._reasoning_effort,
            timeout_seconds=self._timeout_seconds,
        )
        if result.returncode != 0:
            error = _classify_failure(result.stderr)
            if error is not None:
                raise error
            raise ValueError("Codex CLI failed before producing structured output")
        return result.output.strip()


def _json_schema(model: type[BaseModel]) -> dict[str, object]:
    return model.model_json_schema()


def _classify_failure(stderr: str) -> RuntimeError | None:
    normalized = " ".join(stderr.lower().split())
    auth_markers = (
        "not logged in",
        "run codex login",
        "authentication required",
        "unauthorized",
        "invalid credentials",
    )
    if any(marker in normalized for marker in auth_markers):
        return ProviderAuthenticationError("Codex login is required")
    quota_markers = (
        "usage limit",
        "rate limit",
        "too many requests",
        "quota",
        "limit reached",
    )
    if any(marker in normalized for marker in quota_markers):
        return ProviderQuotaError(
            "Codex usage limit reached",
            retry_at=_relative_retry_at(normalized),
        )
    return None


def _relative_retry_at(message: str) -> datetime | None:
    match = re.search(
        r"(?:try again|resets?).{0,40}?(?P<count>\d+)\s*"
        r"(?P<unit>seconds?|minutes?|hours?)",
        message,
    )
    if match is None:
        return None
    count = int(match.group("count"))
    unit = match.group("unit")
    delta = (
        timedelta(hours=count)
        if unit.startswith("hour")
        else timedelta(minutes=count)
        if unit.startswith("minute")
        else timedelta(seconds=count)
    )
    return datetime.now(UTC) + delta
