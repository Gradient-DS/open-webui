"""A KnowledgeForm with no type must not defeat KnowledgeModel's default.

KnowledgeForm.type is Optional[str] = None; KnowledgeModel.type is a required
str defaulting to 'local'. insert_new_knowledge builds the model from
form_data.model_dump(), which emits type=None and overrides that default, so
every caller that did not set a type raised a pydantic ValidationError and the
route answered 500. create_external_knowledge constructs its form without a
type, so it could never succeed at all.
These tests cover the SQL class that soev-api replaced for the product.
"""

import asyncio

import pytest
from open_webui.models.knowledge import KnowledgeForm, Knowledges, KnowledgeTable


@pytest.fixture(autouse=True)
def sql_knowledge_table(monkeypatch):
    """Keep migration-facing SQL behavior independent of the product singleton."""
    monkeypatch.setitem(globals(), 'Knowledges', KnowledgeTable())


def test_a_form_without_a_type_inserts_with_the_model_default():
    knowledge = asyncio.run(
        Knowledges.insert_new_knowledge('user-under-test', KnowledgeForm(name='no type given', description=''))
    )
    assert knowledge is not None, 'insert returned None; the model rejected the form'
    assert knowledge.type == 'local'


def test_an_explicit_type_still_wins():
    knowledge = asyncio.run(
        Knowledges.insert_new_knowledge(
            'user-under-test', KnowledgeForm(name='explicit type', description='', type='external')
        )
    )
    assert knowledge is not None
    assert knowledge.type == 'external'
