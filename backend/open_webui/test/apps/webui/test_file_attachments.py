"""Tests for the file_attachment table model and CRUD wrapper."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_module_exposes_expected_names():
    from open_webui.models import file_attachments

    # Surface
    assert hasattr(file_attachments, 'FileAttachment')
    assert hasattr(file_attachments, 'FileAttachmentModel')
    assert hasattr(file_attachments, 'FileAttachmentForm')
    assert hasattr(file_attachments, 'FileAttachments')


def test_file_attachment_table_name():
    from open_webui.models.file_attachments import FileAttachment

    assert FileAttachment.__tablename__ == 'file_attachment'


def test_file_attachment_model_round_trip_with_defaults():
    from open_webui.models.file_attachments import FileAttachmentModel

    m = FileAttachmentModel(
        id='att-1',
        file_id='file-1',
        kind='plan_png',
        path='uploads/att-1.png',
        created_at=1716643200,
    )
    assert m.storey is None
    assert m.index == 0
    assert m.content_type == 'image/png'
    assert m.caption == ''


def test_file_attachment_form_required_fields():
    from open_webui.models.file_attachments import FileAttachmentForm

    form = FileAttachmentForm(
        id='att-1',
        file_id='file-1',
        kind='axon_png',
        path='uploads/axon.png',
    )
    assert form.index == 0
    assert form.storey is None
    assert form.content_type == 'image/png'


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Throwaway SQLite + the file_attachment table only."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # get_db_context only passes the caller's session through when this is true.
    monkeypatch.setenv('DATABASE_ENABLE_SESSION_SHARING', 'True')
    import open_webui.internal.db as _db_mod

    monkeypatch.setattr(_db_mod, 'DATABASE_ENABLE_SESSION_SHARING', True)

    from open_webui.internal.db import Base
    from open_webui.models.file_attachments import FileAttachment  # noqa: F401 — registers

    engine = create_engine(f'sqlite:///{tmp_path}/test.db')
    Base.metadata.create_all(engine, tables=[FileAttachment.__table__])
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_insert_then_get_by_id(db):
    from open_webui.models.file_attachments import FileAttachmentForm, FileAttachments

    inserted = FileAttachments.insert_new_attachment(
        FileAttachmentForm(
            id='att-1',
            file_id='file-1',
            kind='plan_png',
            storey='Level 1',
            path='uploads/p1.png',
            caption='Level 1 plan',
        ),
        db=db,
    )
    assert inserted is not None
    assert inserted.id == 'att-1'
    assert inserted.storey == 'Level 1'

    got = FileAttachments.get_attachment_by_id('att-1', db=db)
    assert got is not None
    assert got.path == 'uploads/p1.png'


def test_get_attachments_by_file_id_returns_only_matching(db):
    from open_webui.models.file_attachments import FileAttachmentForm, FileAttachments

    for i, fid in enumerate(['fA', 'fA', 'fB']):
        FileAttachments.insert_new_attachment(
            FileAttachmentForm(
                id=f'att-{i}',
                file_id=fid,
                kind='plan_png',
                path=f'uploads/{i}.png',
                index=i,
            ),
            db=db,
        )

    rows = FileAttachments.get_attachments_by_file_id('fA', db=db)
    assert sorted(r.id for r in rows) == ['att-0', 'att-1']


def test_delete_attachments_by_file_id_calls_storage_and_returns_count(db):
    from open_webui.models.file_attachments import FileAttachmentForm, FileAttachments

    for i in range(3):
        FileAttachments.insert_new_attachment(
            FileAttachmentForm(
                id=f'att-{i}',
                file_id='fX',
                kind='plan_png',
                path=f'uploads/x{i}.png',
                index=i,
            ),
            db=db,
        )

    with patch('open_webui.models.file_attachments.Storage') as mock_storage:
        count = FileAttachments.delete_attachments_by_file_id('fX', db=db)

    assert count == 3
    deleted_paths = sorted(c.args[0] for c in mock_storage.delete_file.call_args_list)
    assert deleted_paths == ['uploads/x0.png', 'uploads/x1.png', 'uploads/x2.png']
    assert FileAttachments.get_attachments_by_file_id('fX', db=db) == []


def test_delete_attachments_by_file_id_continues_after_storage_failure(db):
    from open_webui.models.file_attachments import FileAttachmentForm, FileAttachments

    for i in range(2):
        FileAttachments.insert_new_attachment(
            FileAttachmentForm(
                id=f'att-{i}',
                file_id='fY',
                kind='plan_png',
                path=f'uploads/y{i}.png',
                index=i,
            ),
            db=db,
        )

    with patch('open_webui.models.file_attachments.Storage') as mock_storage:
        mock_storage.delete_file.side_effect = [RuntimeError('disk gone'), None]
        count = FileAttachments.delete_attachments_by_file_id('fY', db=db)

    assert count == 2  # both rows dropped even though Storage raised on the first
    assert FileAttachments.get_attachments_by_file_id('fY', db=db) == []


def test_delete_attachment_by_id_returns_true_and_calls_storage(db):
    from open_webui.models.file_attachments import FileAttachmentForm, FileAttachments

    FileAttachments.insert_new_attachment(
        FileAttachmentForm(
            id='att-solo',
            file_id='fS',
            kind='axon_png',
            path='uploads/solo.png',
        ),
        db=db,
    )

    with patch('open_webui.models.file_attachments.Storage') as mock_storage:
        ok = FileAttachments.delete_attachment_by_id('att-solo', db=db)

    assert ok is True
    mock_storage.delete_file.assert_called_once_with('uploads/solo.png')
    assert FileAttachments.get_attachment_by_id('att-solo', db=db) is None


def test_delete_attachment_by_id_returns_false_when_missing(db):
    from open_webui.models.file_attachments import FileAttachments

    ok = FileAttachments.delete_attachment_by_id('att-not-here', db=db)

    assert ok is False


@pytest.mark.skip(reason='async migration follow-up: rewrite stubs for get_async_db_context + asyncio.to_thread')
def test_files_delete_file_by_id_cascades_to_attachments(monkeypatch):
    """Deleting a Files row also drops its attachments and Storage paths."""
    from open_webui.models import files as files_mod

    cascaded_for: list[str] = []

    class SpyAttachments:
        @staticmethod
        def delete_attachments_by_file_id(file_id, db=None):
            cascaded_for.append(file_id)
            return 0

    monkeypatch.setattr('open_webui.models.file_attachments.FileAttachments', SpyAttachments)

    # Don't actually touch a DB; stub the inner query so the method returns True.
    class StubQuery:
        def filter_by(self, **_kw):
            return self

        def filter(self, *_a, **_kw):
            return self

        def delete(self, *_a, **_kw):
            return 0

    class StubSession:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def query(self, *_a, **_kw):
            return StubQuery()

        def commit(self):
            return None

    monkeypatch.setattr(files_mod, 'get_db_context', lambda _db=None: StubSession())

    assert files_mod.Files.delete_file_by_id('file-cascade-1') is True
    assert cascaded_for == ['file-cascade-1']


@pytest.mark.skip(reason='async migration follow-up: rewrite stubs for get_async_db_context + asyncio.to_thread')
def test_files_delete_files_by_ids_cascades_in_bulk(monkeypatch):
    from open_webui.models import files as files_mod

    bulk_calls: list[tuple] = []

    class SpyAttachments:
        @staticmethod
        def delete_attachments_by_file_ids(file_ids, db=None):
            bulk_calls.append((file_ids, db))
            return 0

    monkeypatch.setattr('open_webui.models.file_attachments.FileAttachments', SpyAttachments)

    class StubQuery:
        def filter(self, *_a, **_kw):
            return self

        def delete(self, *_a, **_kw):
            return 0

    class StubSession:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def query(self, *_a, **_kw):
            return StubQuery()

        def commit(self):
            return None

    monkeypatch.setattr(files_mod, 'get_db_context', lambda _db=None: StubSession())

    assert files_mod.Files.delete_files_by_ids(['a', 'b', 'c']) is True
    # Must be called exactly once with all ids — not per-id
    assert len(bulk_calls) == 1
    assert sorted(bulk_calls[0][0]) == ['a', 'b', 'c']


@pytest.mark.skip(reason='async migration follow-up: rewrite stubs for get_async_db_context + asyncio.to_thread')
def test_files_delete_all_files_calls_wipe(monkeypatch):
    from open_webui.models import files as files_mod

    wiped: list[bool] = []

    class SpyAttachments:
        @staticmethod
        def delete_all_attachments(db=None):
            wiped.append(True)
            return 0

        @staticmethod
        def delete_attachments_by_file_id(file_id, db=None):  # unused on this path
            return 0

    monkeypatch.setattr('open_webui.models.file_attachments.FileAttachments', SpyAttachments)

    class StubQuery:
        def delete(self, *_a, **_kw):
            return 0

    class StubSession:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def query(self, *_a, **_kw):
            return StubQuery()

        def commit(self):
            return None

    monkeypatch.setattr(files_mod, 'get_db_context', lambda _db=None: StubSession())

    assert files_mod.Files.delete_all_files() is True
    assert wiped == [True]


@pytest.mark.skip(reason='async migration follow-up: rewrite stubs for get_async_db_context + asyncio.to_thread')
def test_files_delete_file_by_id_returns_false_when_cascade_raises(monkeypatch):
    """If the attachment cascade fails, Files.delete_file_by_id returns False."""
    from open_webui.models import files as files_mod

    class ExplodingAttachments:
        @staticmethod
        def delete_attachments_by_file_id(file_id, db=None):
            raise RuntimeError('boom')

    monkeypatch.setattr(
        'open_webui.models.file_attachments.FileAttachments',
        ExplodingAttachments,
    )

    class StubQuery:
        def filter_by(self, **_kw):
            return self

        def filter(self, *_a, **_kw):
            return self

        def delete(self, *_a, **_kw):
            return 0

    class StubSession:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def query(self, *_a, **_kw):
            return StubQuery()

        def commit(self):
            return None

    monkeypatch.setattr(files_mod, 'get_db_context', lambda db=None: StubSession())

    assert files_mod.Files.delete_file_by_id('file-1') is False
