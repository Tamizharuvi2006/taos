from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .dynamic_answer_schema import get_schema


def build_sections(*, schema_key: str, section_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    schema = get_schema(schema_key)
    out: List[Dict[str, Any]] = []
    for heading in schema.headings:
        value = section_map.get(heading) or section_map.get(heading.lower())
        if not value:
            continue
        content = ""
        bullets: List[str] = []
        if isinstance(value, dict):
            content = str(value.get("content") or "").strip()
            bullets = [str(item).strip() for item in value.get("bullets") or [] if str(item).strip()]
        elif isinstance(value, (list, tuple)):
            bullets = [str(item).strip() for item in value if str(item).strip()]
        else:
            content = str(value).strip()
        if not content and not bullets:
            continue
        out.append(
            {
                "key": _key_for_heading(heading),
                "title": heading,
                "content": content,
                "bullets": bullets,
            }
        )
    return out


def render_sections(sections: Iterable[Dict[str, Any]]) -> str:
    chunks: List[str] = []
    for section in sections:
        title = str(section.get("title") or "Section").strip()
        content = str(section.get("content") or "").strip()
        bullets = [str(item).strip() for item in section.get("bullets") or [] if str(item).strip()]
        chunks.append(f"{title}")
        if content:
            chunks.append(content)
        chunks.extend(f"- {item}" for item in bullets)
        chunks.append("")
    return "\n".join(chunks).strip()


def _key_for_heading(heading: str) -> str:
    return str(heading or "").strip().lower().replace("/", " ").replace("-", " ").replace(" ", "_")
