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
