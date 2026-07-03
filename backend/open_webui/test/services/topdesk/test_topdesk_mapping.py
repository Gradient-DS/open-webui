"""TOPdesk REST field-mapping helpers — pure extractors + scope/visibility filter.

No network, no config — just the nested-shape readers in
``services/topdesk/mapping.py``. Plain (sync) tests.
"""

from __future__ import annotations

from open_webui.services.topdesk import mapping


def _item(**overrides):
    base = {
        'id': 'id-1',
        'number': 'KI 0001',
        'modificationDate': '2026-05-21T14:30:00Z',
        'translation': {
            'language': 'en',
            'content': {
                'title': 'How to reset your password',
                'description': 'A guide.',
                'content': '<p>body</p>',
                'keywords': 'password, reset login  account',
            },
        },
        'status': {'id': 'st', 'name': 'PUBLISHED'},
        'visibility': {'sspVisibility': 'VISIBLE', 'publicKnowledgeItem': False},
        'urls': {'operator': '/op', 'ssp': '/ssp'},
        'parent': {'id': 'parent-1', 'name': 'Account'},
    }
    base.update(overrides)
    return base


# ── Field extractors ──────────────────────────────────────────────────────────


def test_title_falls_back_to_number():
    assert mapping.item_title(_item()) == 'How to reset your password'
    no_title = _item(translation={'language': 'en', 'content': {}})
    assert mapping.item_title(no_title) == 'KI 0001'


def test_body_html_and_description():
    assert mapping.item_body_html(_item()) == '<p>body</p>'
    assert mapping.item_description(_item()) == 'A guide.'


def test_keywords_split_from_comma_and_space_string():
    assert mapping.item_keywords(_item()) == ['password', 'reset', 'login', 'account']


def test_keywords_tolerates_list_and_empty():
    assert mapping.item_keywords(_item(translation={'content': {'keywords': ['a', 'b']}})) == ['a', 'b']
    assert mapping.item_keywords(_item(translation={'content': {}})) == []


def test_language_status_visibility_parent():
    item = _item()
    assert mapping.item_language(item) == 'en'
    assert mapping.item_status_name(item) == 'PUBLISHED'
    assert mapping.item_ssp_visibility(item) == 'VISIBLE'
    assert mapping.item_parent_id(item) == 'parent-1'
    assert mapping.item_parent_id(_item(parent=None)) == ''


def test_web_url_prefixes_relative_and_prefers_public():
    base = 'https://t.topdesk.net'
    # ssp preferred over operator when no public.
    assert mapping.item_web_url(_item(), base) == 'https://t.topdesk.net/ssp'
    # public wins.
    with_public = _item(urls={'operator': '/op', 'ssp': '/ssp', 'public': '/pub'})
    assert mapping.item_web_url(with_public, base) == 'https://t.topdesk.net/pub'
    # absolute url passed through unchanged.
    abs_url = _item(urls={'public': 'https://elsewhere.example/x'})
    assert mapping.item_web_url(abs_url, base) == 'https://elsewhere.example/x'
    # no urls → empty.
    assert mapping.item_web_url(_item(urls={}), base) == ''


# ── should_sync (scope/visibility filter) ─────────────────────────────────────


def test_should_sync_archived_always_excluded():
    assert mapping.should_sync(_item(archived=True), 'all') is False
    assert mapping.should_sync(_item(archived=True), 'ssp') is False


def test_should_sync_ssp_scope():
    assert mapping.should_sync(_item(), 'ssp') is True
    hidden = _item(visibility={'sspVisibility': 'NOT_VISIBLE'})
    assert mapping.should_sync(hidden, 'ssp') is False


def test_should_sync_visible_in_period_within_window():
    item = _item(
        visibility={
            'sspVisibility': 'VISIBLE_IN_PERIOD',
            'sspVisibleFrom': '2000-01-01T00:00:00Z',
            'sspVisibleUntil': '2999-01-01T00:00:00Z',
        }
    )
    assert mapping.should_sync(item, 'ssp') is True


def test_should_sync_visible_in_period_outside_window():
    item = _item(
        visibility={
            'sspVisibility': 'VISIBLE_IN_PERIOD',
            'sspVisibleFrom': '2000-01-01T00:00:00Z',
            'sspVisibleUntil': '2000-02-01T00:00:00Z',
        }
    )
    assert mapping.should_sync(item, 'ssp') is False


def test_should_sync_public_scope():
    public = _item(visibility={'sspVisibility': 'NOT_VISIBLE', 'publicKnowledgeItem': True})
    assert mapping.should_sync(public, 'public') is True
    assert mapping.should_sync(_item(), 'public') is False  # publicKnowledgeItem False


def test_should_sync_all_scope_includes_not_visible():
    hidden = _item(visibility={'sspVisibility': 'NOT_VISIBLE', 'publicKnowledgeItem': False})
    assert mapping.should_sync(hidden, 'all') is True


def test_should_sync_unknown_scope_falls_back_to_ssp():
    assert mapping.should_sync(_item(), 'bogus') is True
    assert mapping.should_sync(_item(visibility={'sspVisibility': 'NOT_VISIBLE'}), 'bogus') is False
