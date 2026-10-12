"""Document export service — single-body Markdown → DOCX."""

from io import BytesIO

from open_webui.utils.chat_export import _md_to_html


def generate_document_docx(title: str, markdown: str) -> bytes:
    """Render a single markdown document as DOCX via python-docx + htmldocx."""
    from docx import Document
    from docx.shared import Pt
    from htmldocx import HtmlToDocx

    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Calibri'
    style.font.size = Pt(11)

    body_html = _md_to_html(markdown)
    parser = HtmlToDocx()
    parser.add_html_to_document(f'<div>{body_html}</div>', doc)

    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.read()
