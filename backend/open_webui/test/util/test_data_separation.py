from open_webui.utils.data_separation import (
    classify_file,
    request_mixes_data_sources,
)


def test_classify_web_search():
    assert classify_file({'type': 'web_search'}) == 'open_internet'


def test_classify_webpage_url():
    assert classify_file({'type': 'text', 'url': 'https://x.com'}) == 'open_internet'


def test_classify_text_without_url_is_neither():
    # a bare text item (not a fetched page) is not open-internet
    assert classify_file({'type': 'text'}) is None


def test_classify_internal_types():
    for t in ('file', 'image', 'collection', 'folder', 'chat', 'note'):
        assert classify_file({'type': t}) == 'internal', t


def test_classify_unknown_and_garbage():
    assert classify_file({'type': 'mystery'}) is None
    assert classify_file({}) is None
    assert classify_file(None) is None
    assert classify_file('nope') is None


def test_no_conflict_when_only_open_internet():
    assert request_mixes_data_sources([{'type': 'text', 'url': 'u'}], [], False) is False
    assert request_mixes_data_sources([], [], True) is False  # web search only


def test_no_conflict_when_only_internal():
    assert request_mixes_data_sources([{'type': 'collection'}], [], False) is False


def test_conflict_web_search_plus_file():
    assert request_mixes_data_sources([{'type': 'file'}], [], True) is True


def test_conflict_webpage_url_plus_collection():
    files = [{'type': 'text', 'url': 'u'}, {'type': 'collection'}]
    assert request_mixes_data_sources(files, [], False) is True


def test_conflict_across_history():
    # internal in history, web search requested this turn
    messages = [{'role': 'user', 'files': [{'type': 'file'}]}]
    assert request_mixes_data_sources([], messages, True) is True


def test_history_files_missing_or_none_is_safe():
    messages = [{'role': 'user'}, {'role': 'assistant', 'files': None}]
    assert request_mixes_data_sources(None, messages, True) is False


def test_empty_request_is_no_conflict():
    assert request_mixes_data_sources(None, None, False) is False
