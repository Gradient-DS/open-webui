"""Guards ``_dedup_discovered_files``'s collapse of duplicate feed emissions.

Graph's /delta (and Drive's changes API) may emit the same driveItem more
than once in a single enumeration; per the API contract the LAST occurrence
is authoritative. ``sync()`` used to feed every occurrence through
classification, stubs, and the loader job — inflating the progress total,
the ``Classified N files`` log, and loader ``items_total`` (the observed
"236 shown vs 221 added"), and double-processing items in the loader. The
dedup runs once, centrally, before classification.
"""

from __future__ import annotations

from open_webui.test.services.sync.test_base_worker_classify import _make_worker


def _file_info(item_id: str, **extra) -> dict:
    return {'item': {'id': item_id, 'name': f'{item_id}.pdf'}, 'name': f'{item_id}.pdf', **extra}


def test_dedup_last_occurrence_wins():
    """Graph contract: the later emission carries the newest metadata/hash."""
    worker = _make_worker()
    first = _file_info('F1', relative_path='old/doc.pdf', cloud_hash='h-old')
    last = _file_info('F1', relative_path='new/doc.pdf', cloud_hash='h-new')

    result = worker._dedup_discovered_files([first, last])

    assert result == [last]
    assert result[0]['relative_path'] == 'new/doc.pdf'
    assert result[0]['cloud_hash'] == 'h-new'


def test_dedup_preserves_first_seen_order_for_distinct_ids():
    worker = _make_worker()
    a, b, c = _file_info('A'), _file_info('B'), _file_info('C')
    b_again = _file_info('B', cloud_hash='h2')

    result = worker._dedup_discovered_files([a, b, c, b_again])

    assert [fi['item']['id'] for fi in result] == ['A', 'B', 'C']
    assert result[1] is b_again  # B keeps its slot but the later emission wins


def test_dedup_noop_without_duplicates():
    worker = _make_worker()
    files = [_file_info('A'), _file_info('B'), _file_info('C')]

    result = worker._dedup_discovered_files(files)

    assert result == files


def test_dedup_collapses_single_file_source_overlapping_folder():
    """A single-file source that is also inside a picked folder used to be
    submitted twice; same item id ⇒ one entry regardless of source_type."""
    worker = _make_worker()
    from_folder = _file_info('F1', source_type='folder', source_item_id='FOLDER')
    from_single = _file_info('F1', source_type='file', source_item_id=None)

    result = worker._dedup_discovered_files([from_folder, from_single])

    assert len(result) == 1
    assert result[0] is from_single  # last occurrence wins
