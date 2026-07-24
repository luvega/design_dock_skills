#!/usr/bin/env python3
"""Validate the public release surface without network access."""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TEXT_ROOTS = [
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    ROOT / "THIRD_PARTY_NOTICES.md",
    ROOT / "docs",
    ROOT / "examples",
    ROOT / "skills",
    ROOT / ".github",
    ROOT / "tools",
]
REQUIRED_FILES = [
    ROOT / "README.md",
    ROOT / "requirements.txt",
    ROOT / "docs/assets/project-icon.png",
    ROOT / "docs/assets/workflow-overview.png",
    ROOT / "docs/assets/method-taxonomy.png",
    ROOT / "docs/assets/evidence-boundary.png",
    ROOT / "docs/methods-and-sources.md",
    ROOT / "docs/testing-and-evaluation.md",
    ROOT / "skills/baker-protein-design/SKILL.md",
    ROOT / "skills/baker-protein-design/agents/openai.yaml",
    ROOT / "examples/test-data/ligands-50.synthetic.csv",
]
PRODUCTION_SCRIPTS = [
    path
    for path in (ROOT / "skills/baker-protein-design/scripts").glob("*.py")
    if not path.name.startswith("test_")
]
SECRET_PATTERNS = [
    re.compile(r"gh[opusr]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"nvapi-[A-Za-z0-9_-]{16,}"),
    re.compile(
        r"(?i)authorization\s*[:=]\s*[\"']?bearer\s+[A-Za-z0-9._-]{16,}"
    ),
]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")
HTML_SRC = re.compile(r"(?:src|href)=[\"']([^\"']+)[\"']")


def public_text_files() -> list[Path]:
    files: set[Path] = set()
    for item in PUBLIC_TEXT_ROOTS:
        if item.is_file():
            files.add(item)
        elif item.is_dir():
            files.update(
                path
                for path in item.rglob("*")
                if path.is_file()
                and path.suffix.lower()
                in {".md", ".py", ".yaml", ".yml", ".csv", ".json", ".txt"}
            )
    return sorted(files)


def check_required_files(errors: list[str]) -> None:
    for path in REQUIRED_FILES:
        if not path.is_file():
            errors.append(f"missing required file: {path.relative_to(ROOT)}")


def local_link_target(raw: str) -> str | None:
    value = raw.strip().strip("<>")
    if not value or value.startswith(("#", "http://", "https://", "mailto:")):
        return None
    value = value.split("#", 1)[0].split("?", 1)[0]
    return value or None


def check_readme_links(errors: list[str]) -> None:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    for match in [*MARKDOWN_LINK.findall(text), *HTML_SRC.findall(text)]:
        target = local_link_target(match)
        if target is None:
            continue
        resolved = (ROOT / target).resolve()
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            errors.append(f"README link escapes repository: {match}")
            continue
        if not resolved.exists():
            errors.append(f"README local link is missing: {match}")


def check_synthetic_manifest(errors: list[str]) -> None:
    path = ROOT / "examples/test-data/ligands-50.synthetic.csv"
    if not path.is_file():
        return
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 50:
        errors.append(f"synthetic ligand manifest has {len(rows)} rows; expected 50")
    identities = {
        (row.get("ligand_id", ""), row.get("chemical_state_id", "")) for row in rows
    }
    if len(identities) != len(rows):
        errors.append("synthetic ligand manifest contains duplicate identities")
    valid = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
    for index, row in enumerate(rows, start=2):
        for field in ("ligand_id", "chemical_state_id"):
            if not valid.fullmatch(row.get(field, "")):
                errors.append(f"{path.name}:{index} invalid {field}")


def check_no_caches(errors: list[str]) -> None:
    for base in (
        ROOT / "skills",
        ROOT / "docs",
        ROOT / "examples",
        ROOT / "tools",
        ROOT / ".github",
    ):
        for path in base.rglob("*"):
            if path.name == "__pycache__" or path.suffix == ".pyc":
                errors.append(f"cache file in release tree: {path.relative_to(ROOT)}")


def check_production_execution_safety(errors: list[str]) -> None:
    for path in PRODUCTION_SCRIPTS:
        text = path.read_text(encoding="utf-8")
        for blocked in ("shell=True", "Invoke-Expression", "os.system("):
            if blocked in text:
                errors.append(f"{path.name} contains blocked execution form: {blocked}")
    workflow = (ROOT / "skills/baker-protein-design/scripts/docking_workflow.py").read_text(
        encoding="utf-8"
    )
    endpoint = (
        "https://health.api.nvidia.com/v1/"
        "molecular-docking/diffdock/generate"
    )
    if endpoint not in workflow:
        errors.append("current Hosted DiffDock endpoint missing from production adapter")


def check_no_literal_secrets(errors: list[str]) -> None:
    for path in public_text_files():
        if path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                errors.append(
                    f"credential-like literal in public non-test file: "
                    f"{path.relative_to(ROOT)}"
                )


def main() -> int:
    errors: list[str] = []
    checks = [
        check_required_files,
        check_readme_links,
        check_synthetic_manifest,
        check_no_caches,
        check_production_execution_safety,
        check_no_literal_secrets,
    ]
    for check in checks:
        check(errors)
        print(f"[{'PASS' if not errors else 'CHECK'}] {check.__name__}")
    if errors:
        print("\nRelease validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"\nOK: {len(checks)} release checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
