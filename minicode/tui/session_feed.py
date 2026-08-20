"""Compatibility helpers for callers that render a complete session feed."""

from __future__ import annotations

from typing import Any

from minicode.session import format_checkpoint_summary_line
from minicode.tui.chrome import RESET, SUBTLE
from minicode.tui.transcript import format_runtime_summary_line
from minicode.tui.types import TranscriptEntry


def build_session_feed_preamble(
    transcript_entries: list[TranscriptEntry],
    session: Any | None = None,
) -> str:
    """Return styled session metadata to place at the start of the feed."""
    checkpoint_summary_line = format_checkpoint_summary_line(session)
    runtime_summary_line = format_runtime_summary_line(transcript_entries)
    session_metadata = getattr(session, "metadata", None)
    summary_lines = [
        line
        for line in (
            checkpoint_summary_line,
            runtime_summary_line,
            f"readiness-summary: {session_metadata.readiness_summary}"
            if session_metadata and getattr(session_metadata, "readiness_summary", "")
            else "",
            f"instruction-summary: {session_metadata.instruction_summary}"
            if session_metadata and getattr(session_metadata, "instruction_summary", "")
            else "",
            f"hook-summary: {session_metadata.hook_summary}"
            if session_metadata and getattr(session_metadata, "hook_summary", "")
            else "",
            f"delegation-summary: {session_metadata.delegation_summary}"
            if session_metadata and getattr(session_metadata, "delegation_summary", "")
            else "",
            f"extension-summary: {session_metadata.extension_summary}"
            if session_metadata and getattr(session_metadata, "extension_summary", "")
            else "",
        )
        if line
    ]
    if not summary_lines:
        return ""
    return f"{SUBTLE}{f'{RESET}\\n{SUBTLE}'.join(summary_lines)}{RESET}"


def decorate_session_feed_body(
    transcript_body: str,
    transcript_entries: list[TranscriptEntry],
    session: Any | None = None,
) -> str:
    """Backward-compatible helper for callers that render a feed as one string."""
    preamble = build_session_feed_preamble(transcript_entries, session)
    return f"{preamble}\\n\\n{transcript_body}" if preamble else transcript_body
