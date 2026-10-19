# -*- coding: utf-8 -*-
"""Canonical project paths.

All runtime code should resolve project resources through this module so that
starting a command from a subdirectory does not change where data or artifacts
are written.
"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
DATA_COMMON_DIR = DATA_DIR / "common"
DATA_INBOX_DIR = DATA_DIR / "inbox"
DATA_LEGAL_DIR = DATA_DIR / "legal"
DATA_REFERENCES_DIR = DATA_DIR / "references"

OUTPUTS_DIR = PROJECT_ROOT / "outputs"
SKILL_OUTPUTS_DIR = OUTPUTS_DIR / "skills"
PPT_PROJECTS_DIR = OUTPUTS_DIR / "projects"
MEMORY_STORAGE_DIR = OUTPUTS_DIR / "memory"
LETTERS_DIR = OUTPUTS_DIR / "letters"
RAG_STORAGE_DIR = OUTPUTS_DIR / "rag"
ANALYSIS_DIR = OUTPUTS_DIR / "analysis"

SKILLS_DIR = PROJECT_ROOT / "skills"
PROPERTY_SKILLS_DIR = SKILLS_DIR / "property"
PPT_SKILL_DIR = PROPERTY_SKILLS_DIR / "ppt_skill"

MEMORY_FILE = PROJECT_ROOT / "MEMORY.md"


def ensure_output_dirs() -> None:
    """Create the standard artifact directories when a command writes output."""

    for path in (
        SKILL_OUTPUTS_DIR,
        PPT_PROJECTS_DIR,
        MEMORY_STORAGE_DIR,
        LETTERS_DIR,
        RAG_STORAGE_DIR,
        ANALYSIS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
