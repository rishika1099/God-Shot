"""FastAPI app: run, poll, list cached runs, edit, re-render, re-shoot. No Streamlit.

Spec: S09 (.claude/specs/S09*.md) — owner: Siva.
Implement per the spec; keep signatures in sync with it.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="God-Shot")
