"""Load `config/affiliate_programs.json` — single source for affiliate tags and link templates."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]


def affiliate_programs_path() -> Path:
    override = (os.getenv("AFFILIATE_PROGRAMS_PATH") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (_REPO_ROOT / "config" / "affiliate_programs.json").resolve()


def load_affiliate_programs() -> dict[str, Any]:
    """Return parsed JSON or empty dict if missing / invalid."""
    path = affiliate_programs_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


_TAG_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def apply_tag_template(template: str, variables: dict[str, Any]) -> str:
    """Replace ``{{key}}`` placeholders using string values from ``variables``. Unknown keys become empty."""

    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        raw = variables.get(key)
        if raw is None:
            return ""
        return str(raw).strip()

    return _TAG_PATTERN.sub(_sub, template or "")


def build_link_for_program(
    program_id: str,
    *,
    programs_data: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Resolve ``programs.<id>.link_template`` with merged fields; returns None if program missing."""
    data = programs_data if programs_data is not None else load_affiliate_programs()
    programs = data.get("programs")
    if not isinstance(programs, dict):
        return None
    entry = programs.get(program_id)
    if not isinstance(entry, dict):
        return None
    tmpl = entry.get("link_template")
    if not isinstance(tmpl, str) or not tmpl.strip():
        return None
    fields: dict[str, Any] = {"program_id": program_id}
    raw_fields = entry.get("fields")
    if isinstance(raw_fields, dict):
        for k, v in raw_fields.items():
            fields[str(k)] = v
    if extra:
        fields.update(extra)
    return apply_tag_template(tmpl, fields) or None


def global_tag_from_template(
    *,
    programs_data: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Apply top-level ``tag_template`` with ``extra`` (e.g. program_id, product_slug)."""
    data = programs_data if programs_data is not None else load_affiliate_programs()
    tmpl = data.get("tag_template")
    if not isinstance(tmpl, str):
        return ""
    ctx: dict[str, Any] = {}
    if extra:
        ctx.update(extra)
    return apply_tag_template(tmpl, ctx)


def is_program_enabled(program_id: str, programs_data: dict[str, Any] | None = None) -> bool:
    """If ``enabled_programs`` is non-empty, ``program_id`` must be listed. Empty list = no restriction."""
    data = programs_data if programs_data is not None else load_affiliate_programs()
    enabled = data.get("enabled_programs")
    if not isinstance(enabled, list) or len(enabled) == 0:
        return True
    return program_id in enabled


def _product_slug_from_url(product_url: str) -> str:
    u = (product_url or "").strip().rstrip("/")
    if not u:
        return "product"
    try:
        tail = u.split("?", 1)[0].split("/")[-1]
        return tail[:80] if tail else "product"
    except Exception:
        return "product"


def resolve_affiliate_data_for_job(
    affiliate_program_id: str | None,
    *,
    product_url: str,
    extra_fields: dict[str, Any] | None = None,
    programs_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build ``affiliate_data`` for manifest / worker env. All strings come from config + caller ``extra_fields``.
    Returns empty dict if ``affiliate_program_id`` is blank.
    """
    pid = (affiliate_program_id or "").strip()
    if not pid:
        return {}
    data = programs_data if programs_data is not None else load_affiliate_programs()
    if not is_program_enabled(pid, data):
        return {
            "affiliate_program_id": pid,
            "error": "program_not_enabled",
        }
    programs = data.get("programs")
    if not isinstance(programs, dict) or pid not in programs:
        return {
            "affiliate_program_id": pid,
            "error": "unknown_program",
        }
    entry = programs.get(pid)
    if not isinstance(entry, dict):
        return {"affiliate_program_id": pid, "error": "invalid_program_entry"}

    slug = _product_slug_from_url(product_url)
    ctx: dict[str, Any] = {
        "program_id": pid,
        "product_slug": slug,
        "product_url": product_url,
    }
    if extra_fields:
        for k, v in extra_fields.items():
            ctx[str(k)] = v

    link = build_link_for_program(pid, programs_data=data, extra=ctx)

    tag = ""
    per_tmpl = entry.get("tag_template")
    if isinstance(per_tmpl, str) and per_tmpl.strip():
        tag = apply_tag_template(per_tmpl, ctx)
    if not tag:
        tag = global_tag_from_template(programs_data=data, extra=ctx)

    out: dict[str, Any] = {
        "affiliate_program_id": pid,
        "affiliate_link": (link or "").strip(),
        "affiliate_tag": tag,
    }
    if link:
        out["link_ready"] = True
    return out
