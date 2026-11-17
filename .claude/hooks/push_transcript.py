"""
Claude Code SessionEnd hook: save the session conversation into the repo
and push it, so it can be reviewed next to the code.

For each finished session it writes
    transcripts/<date>_<session-id-8>.md     readable conversation
    transcripts/<date>_<session-id-8>.jsonl  raw transcript (same redaction)
commits only those two files and pushes the current branch.

- Email addresses and common token formats are redacted.
- Session ids listed in .claude/hooks/transcript_exclude.txt are skipped.
- Set TRANSCRIPT_NO_PUSH=1 to commit without pushing (for testing).

Manual use:  python push_transcript.py <transcript.jsonl>
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
EXCLUDE_FILE = HOOK_DIR / "transcript_exclude.txt"
OUT_DIR_NAME = "transcripts"
MAX_TOOL_TEXT = 1500  # characters of tool input/output kept in the Markdown

REDACTIONS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"\b(ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}"), "[github-token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "[api-key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[aws-key]"),
]


def redact(text: str) -> str:
    for pattern, repl in REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def shorten(text: str, limit=MAX_TOOL_TEXT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more chars]"


def block_text(content) -> str:
    """Text of a tool_result content field (string or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def to_markdown(entries, session_id: str) -> str:
    out = [f"# Claude Code session `{session_id}`", ""]
    for e in entries:
        kind = e.get("type")
        if kind not in ("user", "assistant") or e.get("isMeta"):
            continue
        msg = e.get("message") or {}
        content = msg.get("content")
        stamp = (e.get("timestamp") or "")[:19].replace("T", " ")

        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue

        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text" and b.get("text", "").strip():
                parts.append(b["text"].strip())
            elif t == "tool_use":
                args = json.dumps(b.get("input", {}), indent=2, ensure_ascii=False)
                parts.append(f"<details><summary>Tool: {b.get('name')}</summary>\n\n"
                             f"```json\n{shorten(args)}\n```\n</details>")
            elif t == "tool_result":
                res = shorten(block_text(b.get("content")))
                if res:
                    parts.append(f"<details><summary>Result</summary>\n\n"
                                 f"```\n{res}\n```\n</details>")
            # thinking / images are left out of the Markdown view
        if not parts:
            continue
        only_results = all(isinstance(b, dict) and b.get("type") == "tool_result"
                           for b in content)
        who = "Tool output" if kind == "user" and only_results else (
            "User" if kind == "user" else "Claude")
        out += [f"### {who} <sub>{stamp}</sub>", "", "\n\n".join(parts), ""]
    return redact("\n".join(out))


def git(repo: Path, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def main():
    if len(sys.argv) > 1:  # manual run
        payload = {"transcript_path": sys.argv[1], "cwd": os.getcwd()}
    else:
        payload = json.load(sys.stdin)

    transcript = Path(payload.get("transcript_path", ""))
    if not transcript.is_file():
        return
    session_id = payload.get("session_id") or transcript.stem

    if EXCLUDE_FILE.is_file():
        excluded = {l.strip() for l in EXCLUDE_FILE.read_text().splitlines()
                    if l.strip() and not l.startswith("#")}
        if session_id in excluded:
            return

    repo_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".")
    top = git(repo_dir, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return  # not a git repo
    repo = Path(top.stdout.strip())

    raw_lines = transcript.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in raw_lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    if not any(e.get("type") == "user" for e in entries):
        return  # empty session

    out_dir = repo / OUT_DIR_NAME
    out_dir.mkdir(exist_ok=True)
    base = f"{datetime.now():%Y-%m-%d}_{session_id[:8]}"
    md_file, jsonl_file = out_dir / f"{base}.md", out_dir / f"{base}.jsonl"
    md_file.write_text(to_markdown(entries, session_id), encoding="utf-8")
    jsonl_file.write_text(redact("\n".join(raw_lines)) + "\n", encoding="utf-8")

    rel = [str(p.relative_to(repo)) for p in (md_file, jsonl_file)]
    git(repo, "add", "-f", *rel)
    # --only: commit just the transcript files, never other staged work
    commit = git(repo, "commit", "--only", "-m",
                 f"Add Claude Code session transcript {session_id[:8]}", "--", *rel)
    if commit.returncode != 0 or os.environ.get("TRANSCRIPT_NO_PUSH") == "1":
        return
    git(repo, "push", "--quiet")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the session end
        print(f"push_transcript: {e}", file=sys.stderr)
