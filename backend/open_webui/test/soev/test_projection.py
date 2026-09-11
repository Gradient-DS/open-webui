"""Pure projections of recorded collection and folder wire values into OWUI models."""

import base64
import copy
import importlib

import pytest

SERVICE = 'owui:service:webui'


@pytest.fixture
def projection(monkeypatch, tmp_path):
    """Isolate upstream model import initialization from deployment configuration."""
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path}/projection.db')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('VECTOR_DB', 'weaviate')
    for name in ('TYPE', 'USER', 'PASSWORD', 'HOST', 'PORT', 'NAME'):
        monkeypatch.setenv(f'DATABASE_{name}', '')
    return importlib.import_module('open_webui.soev.projection')


@pytest.fixture
def collection():
    """Record the complete Part A collection response without invoking soev-api."""
    return {
        'key': 'kb-1',
        'name': 'Research',
        'description': None,
        'schema_key': 'core',
        'embedder': 'test-embedder',
        'chunking': {},
        'visibility': 'restricted',
        'principals': [SERVICE, 'owui:user:owner', 'owui:user:reader', 'entra:user:external'],
        'writers': ['owui:user:owner'],
        'created_by': 'owui:user:owner',
        'caller_may_write': None,
        'metadata_spec': {},
        'retention_days': None,
        'tags': [],
        'document_count': 3,
        'created_at': '2026-01-01T00:00:00Z',
        'updated_at': '2026-01-02T01:00:00.999+01:00',
    }


@pytest.mark.parametrize('description', [None, '', 'Collection description'])
def test_a_collection_becomes_a_knowledge_model(projection, collection, description):
    """Map all knowledge fields, nullable descriptions, and offset timestamps without mutating the wire value."""
    from open_webui.models.knowledge import KnowledgeModel

    collection['description'] = description
    original = copy.deepcopy(collection)
    result = projection.knowledge_of(collection, service_principal=SERVICE)
    assert isinstance(result, KnowledgeModel)
    assert result.model_dump(exclude={'access_grants'}) == {
        'id': 'kb-1',
        'user_id': 'owner',
        'name': 'Research',
        'description': description or '',
        'type': 'local',
        'meta': {},
        'created_at': 1767225600,
        'updated_at': 1767312000,
        'deleted_at': None,
    }
    assert result.access_grants == projection.grants_of(collection, service_principal=SERVICE)
    assert collection == original


@pytest.mark.parametrize(
    ('created_by', 'owner_id'),
    [
        ('owui:user:owner', 'owner'),
        ('owui:user:gone', 'gone'),
        (None, ''),
        ('entra:user:owner', ''),
        ('owui:group:owner', ''),
        ('owui:user:', ''),
    ],
)
def test_created_by_becomes_the_owner(projection, collection, created_by, owner_id):
    """Derive only OWUI user ids from created_by without a user lookup."""
    collection['created_by'] = created_by
    result = projection.knowledge_of(collection, service_principal=SERVICE)
    assert result.user_id == owner_id


@pytest.mark.parametrize(
    'grants',
    [
        None,
        [],
        [
            {'principal_type': 'user', 'principal_id': 'z', 'permission': 'read'},
            {'principal_type': 'user', 'principal_id': 'a', 'permission': 'read'},
            {'principal_type': 'user', 'principal_id': 'z', 'permission': 'read'},
        ],
    ],
)
def test_readers_always_include_the_service_principal(projection, grants):
    """Creation preserves form text and emits sorted unique ACLs with the service reader."""
    from open_webui.models.knowledge import KnowledgeForm

    form = KnowledgeForm(name='Research', description='Notes', type='remote', access_grants=grants)
    before = form.model_dump()
    readers = [SERVICE, 'owui:user:a', 'owui:user:z'] if grants else [SERVICE]
    assert projection.access_of(grants or [], service_principal=SERVICE) == ('restricted', readers, [])
    assert projection.collection_create_body(form, service_principal=SERVICE) == {
        'name': 'Research',
        'description': 'Notes',
        'visibility': 'restricted',
        'principals': readers,
        'writers': [],
    }
    assert form.model_dump() == before


def test_a_wildcard_read_grant_becomes_public(projection, collection):
    """Public read uses OWUI's user wildcard and never becomes an enumerated principal."""
    grants = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}] * 2
    assert projection.access_of(grants, service_principal=SERVICE) == ('public', [SERVICE], [])
    collection.update(visibility='public', principals=[SERVICE])
    projected = projection.grants_of(collection, service_principal=SERVICE)
    assert [(g.principal_type, g.principal_id, g.permission) for g in projected] == [('user', '*', 'read')]
    assert projection.access_of([g.model_dump() for g in projected], service_principal=SERVICE) == (
        'public',
        [SERVICE],
        [],
    )


def test_write_grants_become_writers_and_back(projection, collection):
    """Round-trip user writers with stable complete grants while omitting owner, service, and foreign refs."""
    from open_webui.models.access_grants import AccessGrantModel

    service = 'owui:user:service-account'
    collection.update(
        principals=[service, 'owui:user:owner', 'owui:user:reader', 'entra:user:foreign'],
        writers=[service, 'owui:user:owner', 'owui:user:writer', 'owui:user:writer', 'entra:user:foreign'],
    )
    before = copy.deepcopy(collection)
    grants = projection.grants_of(collection, service_principal=service)
    assert all(isinstance(g, AccessGrantModel) for g in grants)
    assert {(g.principal_type, g.principal_id, g.permission) for g in grants} == {
        ('user', 'reader', 'read'),
        ('user', 'writer', 'write'),
    }
    assert len({g.id for g in grants}) == len(grants) == 2
    assert all(g.id and g.resource_type == 'knowledge' and g.resource_id == 'kb-1' for g in grants)
    assert all(g.created_at == 1767225600 for g in grants)
    assert grants == projection.grants_of(collection, service_principal=service)
    other = projection.grants_of({**collection, 'key': 'kb-2'}, service_principal=service)
    assert {g.id for g in grants}.isdisjoint(g.id for g in other)
    incoming = [g.model_dump() for g in grants] * 2
    incoming.extend(
        [
            {'principal_type': 'entra', 'principal_id': 'foreign', 'permission': 'read'},
            {'principal_type': 'user', 'principal_id': '*', 'permission': 'write'},
        ]
    )
    assert projection.access_of(incoming, service_principal=service) == (
        'restricted',
        ['owui:user:reader', service],
        ['owui:user:writer'],
    )
    assert collection == before


def test_group_grants_use_the_owui_group_namespace(projection, collection):
    """Keep user and group identities and read and write permissions distinct in both directions."""
    grants = [
        {'principal_type': principal_type, 'principal_id': 'staff', 'permission': permission}
        for principal_type in ('group', 'user')
        for permission in ('read', 'write')
    ]
    visibility, readers, writers = projection.access_of(grants * 2, service_principal=SERVICE)
    assert visibility == 'restricted'
    assert readers == ['owui:group:staff', SERVICE, 'owui:user:staff']
    assert writers == ['owui:group:staff', 'owui:user:staff']
    collection.update(visibility=visibility, principals=readers, writers=writers)
    result = projection.grants_of(collection, service_principal=SERVICE)
    assert {(g.principal_type, g.principal_id, g.permission) for g in result} == {
        (g['principal_type'], g['principal_id'], g['permission']) for g in grants
    }


@pytest.mark.parametrize('key', ['kb-1', '資料-📚'])
@pytest.mark.parametrize('path', [(), ('Research',), ('Research', '資料 📚')])
def test_directory_ids_round_trip(projection, key, path):
    """Use canonical reversible ids and correct parent models, rejecting malformed or ambiguous encodings."""
    encoded = base64.urlsafe_b64encode((key + '\n' + '/'.join(path)).encode()).decode().rstrip('=')
    identifier = projection.directory_id(key, path)
    assert identifier == 'd_' + encoded
    assert projection.directory_of(identifier) == (key, path)
    malformed = ['wrong_' + encoded, 'd_', 'd_!', 'd_a', identifier + '=', identifier + '!', 'd__w', 'd_a2I']
    for raw in ('\nfolder', 'kb\na//b', 'kb\n/folder', 'kb\nfolder/'):
        malformed.append('d_' + base64.urlsafe_b64encode(raw.encode()).decode().rstrip('='))
    for invalid in malformed:
        with pytest.raises(ValueError):
            projection.directory_of(invalid)
    for invalid_key, invalid_path in [('', path), ('kb\nother', path), (key, ('a/b',)), (key, ('',))]:
        with pytest.raises(ValueError):
            projection.directory_id(invalid_key, invalid_path)
    if not path:
        with pytest.raises(ValueError):
            projection.directory_model(key, path, created_at=123, owner_id='owner')
        return
    model = projection.directory_model(key, path, created_at=123, owner_id='owner')
    assert model.model_dump() == {
        'id': identifier,
        'knowledge_id': key,
        'name': path[-1],
        'user_id': 'owner',
        'parent_id': projection.directory_id(key, path[:-1]) if len(path) > 1 else None,
        'created_at': 123,
        'updated_at': 123,
    }


def test_the_projection_is_total(projection, collection):
    """Require every upstream knowledge field to be projected explicitly, including fields with defaults."""
    from open_webui.models.knowledge import KnowledgeModel

    result = projection.knowledge_of(collection, service_principal=SERVICE)
    assert set(KnowledgeModel.model_fields) == set(result.model_dump())
    assert set(KnowledgeModel.model_fields) == result.model_fields_set
