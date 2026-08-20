from __future__ import annotations

from typing import Any

from minicode.cli_commands import SLASH_COMMANDS, find_matching_slash_commands
from minicode.tui.chrome import _cached_terminal_size, render_banner
from minicode.tui.state import ScreenState, TtyAppArgs
from minicode.tui.chrome import get_permission_prompt_max_scroll_offset
from minicode.tui.tool_helpers import _get_session_stats
from minicode.tui.transcript import (
    get_transcript_max_scroll_offset,
    get_transcript_total_line_count,
)


_HEADER_LINES_ESTIMATE = 11
_COMPACT_HEADER_LINES_ESTIMATE = 0
_PROMPT_LINES_ESTIMATE = 6
_FOOTER_LINES = 2
_GAPS = 2
_TRANSCRIPT_FRAME_LINES = 4


def _get_transcript_body_capacity(*, compact_header: bool) -> int:
    _, rows = _cached_terminal_size()
    rows = max(24, rows)
    chrome_overhead = (
        (_COMPACT_HEADER_LINES_ESTIMATE if compact_header else _HEADER_LINES_ESTIMATE)
        + _PROMPT_LINES_ESTIMATE
        + _FOOTER_LINES
        + _GAPS
        + _TRANSCRIPT_FRAME_LINES
    )
    return max(6, rows - chrome_overhead)


def _is_session_feed_compact(state: ScreenState) -> bool:
    """Expand only after accumulated content outgrows the Workspace layout.

    This deliberately does *not* read the wheel position or follow-tail flag:
    browsing history must never resize the session-feed panel.  The Workspace
    card is displaced once the accumulated transcript needs its rows, and the
    resulting feed height stays stable for every scroll position.
    """
    if not state.transcript:
        return False
    regular_feed_rows = _get_transcript_body_capacity(compact_header=False)
    transcript_rows = get_transcript_total_line_count(
        state.transcript,
        state.transcript_revision,
    )
    return transcript_rows > regular_feed_rows


def _get_transcript_body_lines(args: TtyAppArgs, state: ScreenState) -> int:
    """Size the feed for the current position in the scrolling conversation."""
    del args
    return _get_transcript_body_capacity(
        compact_header=_is_session_feed_compact(state)
    )


def _get_transcript_content_max_scroll_offset(
    args: TtyAppArgs,
    state: ScreenState,
) -> int:
    """Return the scroll range occupied by transcript entries alone."""
    body_lines = _get_transcript_body_lines(args, state)
    return get_transcript_max_scroll_offset(
        state.transcript,
        body_lines,
        state.transcript_revision,
    )


def _get_workspace_scroll_extent(args: TtyAppArgs, state: ScreenState) -> int:
    """Count the standalone Workspace card plus its separation from the feed."""
    if not _is_session_feed_compact(state):
        return 0
    workspace_panel = render_banner(
        args.runtime,
        args.cwd,
        args.permissions.get_summary(),
        _get_session_stats(args, state),
    )
    return len(workspace_panel.splitlines()) + _GAPS


def _get_max_transcript_scroll_offset(args: TtyAppArgs, state: ScreenState) -> int:
    """Include the extra upper range that reveals the Workspace card."""
    return (
        _get_transcript_content_max_scroll_offset(args, state)
        + _get_workspace_scroll_extent(args, state)
    )


def _scroll_transcript_by(args: TtyAppArgs, state: ScreenState, delta: int) -> bool:
    max_offset = _get_max_transcript_scroll_offset(args, state)
    next_offset = max(0, min(max_offset, state.transcript_scroll_offset + delta))
    if next_offset == state.transcript_scroll_offset:
        return False
    state.transcript_scroll_offset = next_offset
    state.transcript_follow_tail = next_offset == 0
    return True


def _jump_transcript_to_edge(args: TtyAppArgs, state: ScreenState, target: str) -> bool:
    next_offset = _get_max_transcript_scroll_offset(args, state) if target == "top" else 0
    if next_offset == state.transcript_scroll_offset:
        return False
    state.transcript_scroll_offset = next_offset
    state.transcript_follow_tail = next_offset == 0
    return True


def _follow_transcript_tail(state: ScreenState) -> None:
    """Keep live output in view without interrupting a user who is browsing."""
    if state.transcript_follow_tail:
        state.transcript_scroll_offset = 0


def _resume_transcript_following(state: ScreenState) -> None:
    """Return to the newest event when the user starts a new interaction."""
    state.transcript_follow_tail = True
    state.transcript_scroll_offset = 0


def _scroll_pending_approval_by(state: ScreenState, delta: int) -> bool:
    pending = state.pending_approval
    if not pending or not pending.details_expanded:
        return False
    max_offset = get_permission_prompt_max_scroll_offset(pending.request, expanded=True)
    next_offset = max(0, min(max_offset, pending.details_scroll_offset + delta))
    if next_offset == pending.details_scroll_offset:
        return False
    pending.details_scroll_offset = next_offset
    return True


def _toggle_pending_approval_expand(state: ScreenState) -> bool:
    pending = state.pending_approval
    if not pending or pending.request.get("kind") != "edit":
        return False
    pending.details_expanded = not pending.details_expanded
    pending.details_scroll_offset = 0
    return True


def _move_pending_approval_selection(state: ScreenState, delta: int) -> bool:
    pending = state.pending_approval
    if not pending or pending.feedback_mode:
        return False
    total = len(pending.request.get("choices", []))
    if total <= 0:
        return False
    pending.selected_choice_index = (pending.selected_choice_index + delta + total) % total
    return True


def _history_up(state: ScreenState) -> bool:
    if not state.history or state.history_index <= 0:
        return False
    if state.history_index == len(state.history):
        state.history_draft = state.input
    state.history_index -= 1
    state.input = state.history[state.history_index] if state.history_index < len(state.history) else ""
    state.cursor_offset = len(state.input)
    return True


def _history_down(state: ScreenState) -> bool:
    if state.history_index >= len(state.history):
        return False
    state.history_index += 1
    state.input = (
        state.history_draft
        if state.history_index == len(state.history)
        else (state.history[state.history_index] if state.history_index < len(state.history) else "")
    )
    state.cursor_offset = len(state.input)
    return True


def _get_visible_commands(input_text: str) -> list[Any]:
    if not input_text.startswith("/"):
        return []
    if input_text == "/":
        return SLASH_COMMANDS
    matches = find_matching_slash_commands(input_text)
    return [cmd for cmd in SLASH_COMMANDS if getattr(cmd, "usage", str(cmd)) in matches]
