import black
import logging
import markdown

from open_webui.models.chats import ChatTitleMessagesForm
from open_webui.config import DATA_DIR, ENABLE_ADMIN_EXPORT
from open_webui.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from starlette.responses import FileResponse


from open_webui.utils.misc import get_gravatar_url
from open_webui.utils.pdf_generator import PDFGenerator
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.code_interpreter import execute_code_jupyter


log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/gravatar")
async def get_gravatar(email: str, user=Depends(get_verified_user)):
    return get_gravatar_url(email)


class CodeForm(BaseModel):
    code: str


@router.post("/code/format")
async def format_code(form_data: CodeForm, user=Depends(get_admin_user)):
    try:
        formatted_code = black.format_str(form_data.code, mode=black.Mode())
        return {"code": formatted_code}
    except black.NothingChanged:
        return {"code": form_data.code}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/code/execute")
async def execute_code(
    request: Request, form_data: CodeForm, user=Depends(get_verified_user)
):
    if request.app.state.config.CODE_EXECUTION_ENGINE == "jupyter":
        output = await execute_code_jupyter(
            request.app.state.config.CODE_EXECUTION_JUPYTER_URL,
            form_data.code,
            (
                request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH_TOKEN
                if request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH == "token"
                else None
            ),
            (
                request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH_PASSWORD
                if request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH == "password"
                else None
            ),
            request.app.state.config.CODE_EXECUTION_JUPYTER_TIMEOUT,
        )

        return output
    else:
        raise HTTPException(
            status_code=400,
            detail="Code execution engine not supported",
        )


class MarkdownForm(BaseModel):
    md: str


@router.post("/markdown")
async def get_html_from_markdown(
    form_data: MarkdownForm, user=Depends(get_verified_user)
):
    return {"html": markdown.markdown(form_data.md)}


class ChatForm(BaseModel):
    title: str
    messages: list[dict]


@router.post("/pdf")
async def download_chat_as_pdf(
    form_data: ChatTitleMessagesForm, user=Depends(get_verified_user)
):
    try:
        pdf_bytes = PDFGenerator(form_data).generate_chat_pdf()

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment;filename=chat.pdf"},
        )
    except Exception as e:
        log.exception(f"Error generating PDF: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/db/download")
async def download_db(user=Depends(get_admin_user)):
    if not ENABLE_ADMIN_EXPORT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    from open_webui.internal.db import engine

    if engine.name != "sqlite":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DB_NOT_SQLITE,
        )
    return FileResponse(
        engine.url.database,
        media_type="application/octet-stream",
        filename="webui.db",
    )


@router.get("/db/export")
async def export_db_json(user=Depends(get_admin_user)):
    """
    Export database as JSON.

    Works with both SQLite and PostgreSQL.
    Returns JSON file with all tables.
    """
    import json
    import time
    from io import BytesIO
    from starlette.responses import StreamingResponse
    from open_webui.internal.db import engine, get_db
    from sqlalchemy import inspect, text

    if not ENABLE_ADMIN_EXPORT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    export_data = {
        "export_version": "1.0",
        "exported_at": int(time.time()),
        "database_type": engine.name,
        "tables": {},
    }

    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    with get_db() as db:
        for table_name in table_names:
            # Skip alembic version table
            if table_name == "alembic_version":
                continue

            try:
                # Quote table name to handle reserved words like 'group' and 'user'
                result = db.execute(text(f'SELECT * FROM "{table_name}"'))
                columns = result.keys()
                rows = []
                for row in result:
                    row_dict = {}
                    for i, col in enumerate(columns):
                        value = row[i]
                        # Handle non-JSON-serializable types
                        if isinstance(value, bytes):
                            value = value.hex()
                        elif hasattr(value, 'isoformat'):
                            value = value.isoformat()
                        row_dict[col] = value
                    rows.append(row_dict)
                export_data["tables"][table_name] = {
                    "columns": list(columns),
                    "row_count": len(rows),
                    "rows": rows,
                }
            except Exception as e:
                log.error(f"Error exporting table {table_name}: {e}")
                export_data["tables"][table_name] = {
                    "error": str(e),
                }

    # Create JSON file in memory
    json_bytes = json.dumps(export_data, indent=2, default=str).encode('utf-8')
    buffer = BytesIO(json_bytes)

    filename = f"openwebui-export-{int(time.time())}.json"

    return StreamingResponse(
        buffer,
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
        }
    )
