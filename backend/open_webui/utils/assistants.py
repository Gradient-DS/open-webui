"""[Gradient] Pure composition of an assistant with an independently chosen LLM."""

from copy import deepcopy
from typing import TYPE_CHECKING

from open_webui.utils.chat_variables import get_chat_variables_schema

if TYPE_CHECKING:
    from open_webui.models.models import ModelModel


def effective_capabilities(llm_caps: dict | None, assistant_caps: dict | None) -> dict:
    """Restrict vision to both parties and let explicit assistant choices win otherwise."""
    llm_caps, assistant_caps = llm_caps or {}, assistant_caps or {}
    return deepcopy(
        {
            **llm_caps,
            **assistant_caps,
            'vision': bool(llm_caps.get('vision', True) and assistant_caps.get('vision', True)),
        }
    )


def build_assistant_model(assistant_row: 'ModelModel', llm_model: dict) -> dict:
    """Preserve LLM dispatch fields while attaching private assistant metadata."""
    model = deepcopy(llm_model)
    info = deepcopy(assistant_row.model_dump())
    params = info.pop('params', {})
    info['base_model_id'] = None
    meta = info.setdefault('meta', {})
    schema = get_chat_variables_schema(params.get('system'))
    if schema:
        meta['chat_variables_schema'] = schema
    meta['capabilities'] = effective_capabilities(
        ((llm_model.get('info') or {}).get('meta') or {}).get('capabilities'),
        meta.get('capabilities'),
    )
    model['info'] = info
    model['assistant_id'] = assistant_row.id
    return model


def merged_model_info(llm_row_or_none: 'ModelModel | None', assistant_row: 'ModelModel') -> 'ModelModel':
    """Merge LLM overrides below assistant params without retaining legacy routing."""
    base = deepcopy(llm_row_or_none.params.model_dump()) if llm_row_or_none else {}
    override = deepcopy(assistant_row.params.model_dump())
    params = {**base, **override}
    # Match merge_model_params, including its shallow custom-parameter merge.
    base_custom, override_custom = base.get('custom_params'), override.get('custom_params')
    if isinstance(base_custom, dict) and (override_custom is None or isinstance(override_custom, dict)):
        params['custom_params'] = {**base_custom, **(override_custom or {})}
    return assistant_row.model_copy(
        deep=True,
        update={'base_model_id': None, 'params': assistant_row.params.__class__(**params)},
    )
