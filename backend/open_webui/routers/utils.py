from __future__ import annotations

import logging

import black
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from open_webui.config import DATA_DIR, ENABLE_DOCX_EXPORT
from open_webui.constants import ERROR_MESSAGES
from open_webui.models.chats import ChatTitleMessagesForm
from open_webui.models.config import Config
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.code_interpreter import execute_code_jupyter
from open_webui.utils.misc import get_gravatar_url
from open_webui.utils.pdf_generator import PDFGenerator
from pydantic import BaseModel

log = logging.getLogger(__name__)


def _safe_filename(title: str, ext: str, prefix: str = 'chat') -> str:
    """Build a Content-Disposition header value safe for HTTP headers.

    Uses RFC 5987 filename* for UTF-8 support, with an ASCII fallback.
    """
    from urllib.parse import quote

    # ASCII-safe fallback: strip non-ASCII
    ascii_name = title.encode('ascii', 'ignore').decode('ascii').strip() or prefix
    fallback = f'{prefix}-{ascii_name}.{ext}'
    utf8_name = f'{prefix}-{title}.{ext}'
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(utf8_name)}'


router = APIRouter()


@router.get('/gravatar')
async def get_gravatar(email: str, user=Depends(get_verified_user)):
    return get_gravatar_url(email)


class CodeForm(BaseModel):
    code: str


@router.post('/code/format')
async def format_code(form_data: CodeForm, user=Depends(get_admin_user)):
    try:
        formatted_code = black.format_str(form_data.code, mode=black.Mode())
        return {'code': formatted_code}
    except black.NothingChanged:
        return {'code': form_data.code}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/code/execute')
async def execute_code(request: Request, form_data: CodeForm, user=Depends(get_verified_user)):
    if not await Config.get('code_execution.enable'):
        raise HTTPException(
            status_code=403,
            detail=ERROR_MESSAGES.FEATURE_DISABLED('Code execution'),
        )

    if await Config.get('code_execution.engine') == 'jupyter':
        output = await execute_code_jupyter(
            await Config.get('code_execution.jupyter.url'),
            form_data.code,
            (
                await Config.get('code_execution.jupyter.auth_token')
                if await Config.get('code_execution.jupyter.auth') == 'token'
                else None
            ),
            (
                await Config.get('code_execution.jupyter.auth_password')
                if await Config.get('code_execution.jupyter.auth') == 'password'
                else None
            ),
            await Config.get('code_execution.jupyter.timeout'),
        )

        return output
    else:
        raise HTTPException(
            status_code=400,
            detail=ERROR_MESSAGES.DEFAULT('Code execution engine not supported'),
        )


class ChatForm(BaseModel):
    title: str
    messages: list[dict]


@router.post('/pdf')
async def download_chat_as_pdf(form_data: ChatTitleMessagesForm, user=Depends(get_verified_user)):
    try:
        pdf_bytes = PDFGenerator(form_data).generate_chat_pdf()

        return Response(
            content=pdf_bytes,
            media_type='application/pdf',
            headers={'Content-Disposition': 'attachment;filename=chat.pdf'},
        )
    except Exception as e:
        log.exception(f'Error generating PDF: {e}')
        raise HTTPException(status_code=400, detail=str(e))


class ChatExportForm(BaseModel):
    title: str
    messages: list[dict]
    include_chrome: bool = True


@router.post('/chat/pdf')
async def export_chat_as_pdf(
    form_data: ChatExportForm,
    user=Depends(get_verified_user),
):
    """Export chat as a properly rendered PDF with citations and sources."""
    from open_webui.services.chat_export import generate_pdf

    try:
        pdf_bytes = generate_pdf(form_data.title, form_data.messages, include_chrome=form_data.include_chrome)
        return Response(
            content=pdf_bytes,
            media_type='application/pdf',
            headers={'Content-Disposition': _safe_filename(form_data.title, 'pdf')},
        )
    except Exception as e:
        log.exception(f'Error generating PDF: {e}')
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/chat/docx')
async def export_chat_as_docx(
    form_data: ChatExportForm,
    user=Depends(get_verified_user),
):
    """Export chat as a Word document with citations and sources."""
    if not ENABLE_DOCX_EXPORT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='DOCX export is disabled')
    from open_webui.services.chat_export import generate_docx

    try:
        docx_bytes = generate_docx(form_data.title, form_data.messages, include_chrome=form_data.include_chrome)
        return Response(
            content=docx_bytes,
            media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            headers={'Content-Disposition': _safe_filename(form_data.title, 'docx')},
        )
    except Exception as e:
        log.exception(f'Error generating DOCX: {e}')
        raise HTTPException(status_code=500, detail=str(e))
