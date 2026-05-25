"""Tests for the file_attachment table model and CRUD wrapper."""

from __future__ import annotations

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
