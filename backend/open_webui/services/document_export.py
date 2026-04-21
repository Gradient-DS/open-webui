"""Document export service — single-body Markdown → PDF / DOCX."""

import logging
from io import BytesIO
from pathlib import Path

from open_webui.utils.chat_export import _md_to_html

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent / 'templates'


def _render_document_html(title: str, markdown: str) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(default_for_string=True, default=True),
    )
    template = env.get_template('document_export.html')
    body_html = _md_to_html(markdown)
    return template.render(title=title, body=body_html)


def generate_document_pdf(title: str, markdown: str) -> bytes:
    """Render a single markdown document as PDF via WeasyPrint."""
    from weasyprint import HTML

    html_string = _render_document_html(title, markdown)
    return HTML(string=html_string).write_pdf()


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
