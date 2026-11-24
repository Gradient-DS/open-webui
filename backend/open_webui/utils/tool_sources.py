"""[Gradient] External connection selections cross the browser and saved-input boundaries."""

from uuid import UUID


def selected_tool_sources(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 8:
        return []
    try:
        if any(not isinstance(entry, str) for entry in value):
            return []
        parsed = [UUID(entry) for entry in value]
        if len(set(parsed)) != len(parsed):
            return []
    except ValueError:
        return []
    return list(value)
