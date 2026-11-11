#!/usr/bin/env python3
"""Small portable skill-contract validator; does not access accounts or secrets."""
from pathlib import Path
import re
import sys

REQUIRED = (
    "General", "expected_icon_custom_emoji_id", "every page", "decoded pixels",
    "No secrets", "22px", "readback", "existing authenticated", "silhouette",
    "preview-only", "uncertain", "source and rights", "no live Telegram mutations",
)


def validate(root):
    root = Path(root).resolve()
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    errors = []
    if len(text.encode()) > 12_000:
        errors.append("SKILL.md exceeds 12 kB")
    if not text.startswith("---\n") or text.count("\n---") < 1:
        errors.append("Missing YAML frontmatter")
    if not re.search(r"^name: chip-topics-icons$", text, re.M):
        errors.append("Wrong discovery name")
    match = re.search(r'^description: [\"\']?(.+?)[\"\']?$', text, re.M)
    if not match or len(match[1]) > 60 or not match[1].endswith("."):
        errors.append("Description must be at most 60 characters and end with a period")
    for phrase in REQUIRED:
        if phrase not in text:
            errors.append("Missing root contract: " + phrase)
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if "://" in link or link.startswith("#"):
            continue
        target = (root / link.split("#", 1)[0]).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            errors.append("Broken or escaping root link: " + link)
    for path in ("README.md", "LICENSE", "requirements.txt", "topics_icons/cli.py", "tests/test_workflow.py", "evals/prompts.json"):
        if not (root / path).is_file():
            errors.append("Missing package file: " + path)
    return errors


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    problems = validate(root)
    print("\n".join(problems) if problems else "Skill contract: PASS")
    raise SystemExit(bool(problems))
