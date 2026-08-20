from __future__ import annotations
import sys
import time
from typing import Any
from minicode.background_tasks import list_background_tasks
from minicode.tui.chrome import (
    _cached_terminal_size,
    render_banner,
    render_footer_bar,
    render_panel,
    render_permission_prompt,
    render_slash_menu,
    render_status_line,
    render_tool_panel,
    SUBTLE,
    RESET,
)
from minicode.tui.input import render_input_prompt
from minicode.tui.transcript import render_transcript
from minicode.tui.state import TtyAppArgs, ScreenState
from minicode.tui.navigation import (
    _get_transcript_content_max_scroll_offset,
    _get_transcript_body_lines,
    _get_visible_commands,
    _is_session_feed_compact,
)
from minicode.tui.session_feed import decorate_session_feed_body
from minicode.tui.tool_helpers import _get_session_stats
from minicode.tui.types import TranscriptEntry
from minicode.tui.ui_hints import _get_contextual_help

# Rendering — cached header & footer
# ---------------------------------------------------------------------------

# Banner cache: the banner rarely changes (only when cwd, model, or stats change).
_banner_cache: dict[str, tuple[tuple, str]] = {"key": ((), "")}

# Incremental rendering: track last rendered state to avoid full redraw
_last_render_hash: int = 0
_last_render_time: float = 0.0
_transcript_snapshot_cache: dict[
    str,
    tuple[tuple[int, int, int], list[TranscriptEntry]],
] = {}


def _render_header_panel(
    args: TtyAppArgs,
    state: ScreenState,
    *,
    compact: bool = False,
) -> str:
    """Render the top banner panel with model info, cwd, and session stats.
    
    The result is cached to avoid re-rendering when stats haven't changed.
    """
    stats = _get_session_stats(args, state)
    cache_key = (
        args.cwd,
        id(args.runtime),
        stats.get("transcriptCount"),
        stats.get("messageCount"),
        stats.get("skillCount"),
        stats.get("mcpCount"),
        _cached_terminal_size(),
        compact,
    )
    cached = _banner_cache.get("key")
    if cached and cached[0] == cache_key:
        return cached[1]
    result = render_banner(
        args.runtime,
        args.cwd,
        args.permissions.get_summary(),
        stats,
        compact=compact,
    )
    _banner_cache["key"] = (cache_key, result)
    return result


# Footer cache: only changes with status, tool/skill state, background tasks
_footer_cache: dict[str, tuple[tuple, str]] = {"key": ((), "")}


def _render_footer_cached(
    status: str | None,
    tools_enabled: bool,
    skills_enabled: bool,
    background_tasks: list[dict[str, Any]],
) -> str:
    """Render the bottom status bar with caching to reduce flicker.
    
    Shows current operation status, tool/skill availability, and background tasks.
    """
    cache_key = (
        status,
        tools_enabled,
        skills_enabled,
        len(background_tasks),
        _cached_terminal_size(),
    )
    cached = _footer_cache.get("key")
    if cached and cached[0] == cache_key:
        return cached[1]
    result = render_footer_bar(status, tools_enabled, skills_enabled, background_tasks)
    _footer_cache["key"] = (cache_key, result)
    return result


def _render_prompt_panel(state: ScreenState) -> str:
    commands = _get_visible_commands(state.input)
    prompt_body = render_input_prompt(state.input, state.cursor_offset)
    if commands:
        prompt_body += "\n" + render_slash_menu(
            commands,
            min(state.selected_slash_index, len(commands) - 1),
        )
    return render_panel("prompt", prompt_body)


def _compute_render_hash(args: TtyAppArgs, state: ScreenState) -> int:
    """Compute a hash of the current render state to detect if redraw is needed."""
    transcript_rev = state.transcript_revision
    scroll = state.transcript_scroll_offset
    input_hash = hash(state.input)
    cursor = state.cursor_offset
    status = hash(state.status)
    approval = 0
    if state.pending_approval:
        approval = hash((
            state.pending_approval.details_expanded,
            state.pending_approval.details_scroll_offset,
            state.pending_approval.selected_choice_index,
            state.pending_approval.feedback_mode,
            state.pending_approval.feedback_input,
        ))
    recent_tool_state = tuple(
        (tool.get("name"), tool.get("status"))
        for tool in state.recent_tools[-3:]
    )
    term_size = _cached_terminal_size()
    return hash((
        transcript_rev,
        scroll,
        state.transcript_follow_tail,
        input_hash,
        cursor,
        status,
        state.active_tool,
        recent_tool_state,
        approval,
        term_size,
    ))


def _get_transcript_snapshot(state: ScreenState) -> list[TranscriptEntry]:
    cache_key = (id(state.transcript), state.transcript_revision, len(state.transcript))
    cached = _transcript_snapshot_cache.get("key")
    if cached and cached[0] == cache_key:
        return cached[1]

    snapshot = list(state.transcript)
    _transcript_snapshot_cache["key"] = (cache_key, snapshot)
    return snapshot


def _decorate_session_feed_body(
    transcript_body: str,
    transcript_entries: list[TranscriptEntry],
    session: Any | None = None,
) -> str:
    return decorate_session_feed_body(transcript_body, transcript_entries, session)


def _render_scrolling_workspace_view(
    args: TtyAppArgs,
    state: ScreenState,
    feed_panel: str,
    workspace_scroll: int,
) -> str:
    """Reveal Workspace above a fixed-size feed without resizing that feed.

    At the true transcript top, extra upward scroll moves the whole panel
    stack downward.  The Workspace card enters from above while the session
    panel keeps its original height and is merely clipped at the viewport's
    lower edge, as a normal scrolling document would be.
    """
    if workspace_scroll <= 0:
        return feed_panel

    workspace_lines = _render_header_panel(args, state).splitlines() + ["", ""]
    reveal = min(workspace_scroll, len(workspace_lines))
    feed_lines = feed_panel.splitlines()
    return "\n".join(
        workspace_lines[-reveal:] + feed_lines[: max(0, len(feed_lines) - reveal)]
    )


def _render_screen(args: TtyAppArgs, state: ScreenState) -> None:
    global _last_render_hash, _last_render_time
    
    # Quick check: skip render if nothing changed and within throttle
    current_hash = _compute_render_hash(args, state)
    now = time.monotonic()
    if (current_hash == _last_render_hash 
            and now - _last_render_time < 0.016):  # ~60fps cap
        return
    
    background_tasks = list_background_tasks()

    # Build the entire frame into a buffer, then write once
    buf: list[str] = []
    # CSI H + CSI J  (cursor home + erase to end) – avoids full clear flicker
    buf.append("\u001b[H\u001b[J")

    # Workspace and the feed remain separate cards.  Once accumulated history
    # needs the Workspace rows, the feed expands once and keeps that geometry
    # while the user scrolls; only an extra range above transcript history
    # reveals the independent Workspace card.
    feed_owns_viewport = _is_session_feed_compact(state) and not state.pending_approval
    contextual_help = None if feed_owns_viewport else _get_contextual_help(state, args)
    if not feed_owns_viewport:
        buf.append(_render_header_panel(args, state))
        buf.append("\n\n")

    has_skills = len(args.tools.get_skills()) > 0

    if state.pending_approval:
        # Permission approval overlay
        buf.append(
            render_permission_prompt(
                state.pending_approval.request,
                expanded=state.pending_approval.details_expanded,
                scroll_offset=state.pending_approval.details_scroll_offset,
                selected_choice_index=state.pending_approval.selected_choice_index,
                feedback_mode=state.pending_approval.feedback_mode,
                feedback_input=state.pending_approval.feedback_input,
            )
        )
        buf.append("\n\n")
        buf.append(
            render_panel(
                "activity",
                render_tool_panel(state.active_tool, state.recent_tools, background_tasks),
            )
        )
        buf.append("\n\n")
        buf.append(_render_footer_cached(state.status, True, has_skills, background_tasks))
        output = "".join(buf)
        sys.stdout.write(output)
        sys.stdout.flush()
        _last_render_hash = current_hash
        _last_render_time = now
        return

    # Transcript — snapshot the list to avoid IndexError from concurrent
    # agent-thread appends (CPython GIL makes list.append atomic but
    # iteration + append can still race on length vs slot access).
    transcript_snapshot = _get_transcript_snapshot(state)
    body_lines = _get_transcript_body_lines(args, state)
    transcript_content_max = _get_transcript_content_max_scroll_offset(args, state)
    transcript_scroll = min(state.transcript_scroll_offset, transcript_content_max)
    workspace_scroll = max(0, state.transcript_scroll_offset - transcript_content_max)
    if transcript_snapshot:
        transcript_body = render_transcript(
            transcript_snapshot,
            transcript_scroll,
            body_lines,
            state.transcript_revision,
        )
    else:
        transcript_body = f"{render_status_line(None)}\n\nType /help for commands."
    feed_position = (
        "live"
        if state.transcript_follow_tail
        else f"scroll {state.transcript_scroll_offset}"
    )
    feed_panel = render_panel(
        "session feed",
        transcript_body,
        right_title=f"{len(transcript_snapshot)} events · {feed_position}",
        min_body_lines=body_lines,
    )
    buf.append(_render_scrolling_workspace_view(args, state, feed_panel, workspace_scroll))
    buf.append("\n\n")

    # Prompt
    buf.append(_render_prompt_panel(state))
    buf.append("\n\n")

    # Footer (cached)
    buf.append(_render_footer_cached(state.status, True, has_skills, background_tasks))
    
    # 上下文帮助行
    if contextual_help:
        buf.append(f"\n{SUBTLE}{contextual_help}{RESET}")
    
    output = "".join(buf)
    sys.stdout.write(output)
    sys.stdout.flush()
    _last_render_hash = current_hash
    _last_render_time = now


# ---------------------------------------------------------------------------
