"""[Gradient] The global system prompt (config models.system_prompt), set once for every base model.

A base model uses its own row's prompt when it has one (rows from before the global prompt, kept until an
admin clears them), else the global prompt. An empty prompt box is stored as system: null and counts as no
prompt. Assistants (rows with a base model) keep their own prompt only, as before.
"""

from open_webui.models.config import Config

KEY = 'models.system_prompt'


def _text(value) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


async def global_prompt() -> str | None:
    return _text(await Config.get(KEY, ''))


async def for_model(model_info, fallback: str | None = None) -> str | None:
    """The prompt a model's turns get. fallback is DEFAULT_MODEL_PARAMS' system, below the global prompt."""
    params = model_info.params.model_dump() if model_info is not None and model_info.params else {}
    if own := _text(params.get('system')):
        return own
    if model_info is not None and model_info.base_model_id:
        return _text(fallback)
    return await global_prompt() or _text(fallback)
