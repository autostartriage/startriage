"""Core autotriage logic: collect bug data, send to AI, write output."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any

from .ai import AIProvider, UnknownModelError
from .retry import lp_retry
from .spinner import Spinner

logger = logging.getLogger(__name__)


@dataclass
class MessageInfo:
    author: str
    date: str
    content: str


@dataclass
class BugInfo:
    number: str
    title: str
    description: str
    source_package: str
    status: str
    importance: str
    tags: list[str] = field(default_factory=list)
    messages: list[MessageInfo] = field(default_factory=list)


def _load_system_prompt() -> str:
    """Load the triage agent prompt shipped as package data."""
    prompt_path = files("startriage") / "data" / "agents_prompt.md"
    return prompt_path.read_text(encoding="utf-8")


def _format_bug(bug: BugInfo) -> str:
    """Format a single bug as structured text for the AI prompt."""
    lines = [
        f"## Bug LP #{bug.number}",
        f"Package: {bug.source_package}",
        f"Current status: {bug.status}",
        f"Importance: {bug.importance}",
        f"Tags: {', '.join(bug.tags) if bug.tags else 'none'}",
        f"Title: {bug.title}",
        "",
        "### Description",
        bug.description or "(no description)",
    ]

    if bug.messages:
        lines.append("")
        lines.append("### Comments")
        # Limit to last 20 comments to avoid exceeding context limits
        recent = bug.messages[-20:]
        if len(bug.messages) > 20:
            lines.append(f"(showing last 20 of {len(bug.messages)} comments)")
        for i, msg in enumerate(recent, 1):
            lines.append(f"\n**Comment #{i}** by {msg.author} on {msg.date}:")
            lines.append(msg.content)

    return "\n".join(lines)


@lp_retry()
def bug_info_from_lp_task(task: Any) -> BugInfo:
    """Extract BugInfo from a startriage launchpad Task object."""
    bug = task.lp_task.bug

    messages: list[MessageInfo] = []
    try:
        all_msgs = list(bug.messages)
        for msg in all_msgs[1:]:  # skip first (description)
            author = "Unknown"
            try:
                if msg.owner:
                    author = msg.owner.display_name
            except Exception:
                pass
            messages.append(
                MessageInfo(
                    author=author,
                    date=str(msg.date_created),
                    content=msg.content,
                )
            )
    except Exception as exc:
        logger.debug("Could not fetch messages for LP #%s: %s", task.number, exc)

    return BugInfo(
        number=task.number,
        title=task.short_title,
        description=bug.description,
        source_package=task.src,
        status=task.status,
        importance=task.importance,
        tags=list(task.tags),
        messages=messages,
    )


@lp_retry()
def bug_info_from_lp_bug(lp: Any, bug_number: str) -> BugInfo:
    """Fetch a bug directly from Launchpad by number and build BugInfo."""
    bug = lp.bugs[int(bug_number)]

    # Find the best Ubuntu task for metadata
    source_package = "unknown"
    status = "New"
    importance = "Undecided"

    for lp_task in bug.bug_tasks:
        target_link = str(lp_task.target_link)
        if "ubuntu" in target_link.lower():
            parts = target_link.split("/")
            # look for +source/PKGNAME pattern
            for i, part in enumerate(parts):
                if part == "+source" and i + 1 < len(parts):
                    source_package = parts[i + 1]
                    break
            status = lp_task.status
            importance = lp_task.importance
            break

    messages: list[MessageInfo] = []
    try:
        all_msgs = list(bug.messages)
        for msg in all_msgs[1:]:
            author = "Unknown"
            try:
                if msg.owner:
                    author = msg.owner.display_name
            except Exception:
                pass
            messages.append(
                MessageInfo(
                    author=author,
                    date=str(msg.date_created),
                    content=msg.content,
                )
            )
    except Exception as exc:
        logger.debug("Could not fetch messages for LP #%s: %s", bug_number, exc)

    return BugInfo(
        number=str(bug.id),
        title=bug.title,
        description=bug.description,
        source_package=source_package,
        status=status,
        importance=importance,
        tags=list(bug.tags),
        messages=messages,
    )


async def run_autotriage(
    bugs: list[BugInfo],
    provider: AIProvider,
    output_path: Path | None = None,
) -> Path:
    """Run AI-assisted triage on a list of bugs and write the output file.

    Returns the path to the generated output file.
    """
    system_prompt = _load_system_prompt()

    if output_path is None:
        output_path = Path(f"autotriage-{date.today().isoformat()}.md")

    sections: list[str] = []
    bug_labels = {f"LP #{b.number}" for b in bugs}

    async with Spinner(bug_labels) as spinner:
        for bug in bugs:
            label = f"LP #{bug.number}"
            logger.info("Triaging LP #%s (%s)...", bug.number, bug.source_package)
            try:
                user_prompt = _format_bug(bug)
                response = await provider.complete(system_prompt, user_prompt)
                sections.append(response)
            except UnknownModelError:
                raise
            except Exception as exc:
                logger.error("AI triage failed for LP #%s: %s", bug.number, exc)
                sections.append(
                    f"## LP #{bug.number} — {bug.source_package} — {bug.title}\n\n"
                    f"**Error:** AI triage failed: {exc}\n"
                )
            spinner.done(label)

    content = "\n\n---\n\n".join(sections) + "\n"
    output_path.write_text(content, encoding="utf-8")
    print(f"Autotriage output written to {output_path}")
    return output_path
