import asyncio
import json
import logging
import time
import zipfile
from pathlib import Path
from typing import Any

from open_webui.config import CACHE_DIR, DATA_EXPORT_RETENTION_HOURS
from open_webui.models.chats import Chats
from open_webui.models.feedbacks import Feedbacks
from open_webui.models.files import Files
from open_webui.models.folders import Folders
from open_webui.models.functions import Functions
from open_webui.models.knowledge import Knowledges
from open_webui.models.memories import Memories
from open_webui.models.models import Models
from open_webui.models.notes import Notes
from open_webui.models.prompts import Prompts
from open_webui.models.tags import Tags
from open_webui.models.tools import Tools
from open_webui.models.users import Users
from open_webui.services.export.events import emit_export_status
from open_webui.utils.loop_bridge import run_on_main_loop
from open_webui.storage.provider import Storage

log = logging.getLogger(__name__)

EXPORT_DIR = CACHE_DIR / 'exports'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

EXPORT_SCHEMA_VERSION = '1.0'


class ExportService:
    @staticmethod
    def get_export_dir(user_id: str) -> Path:
        """Get or create the export directory for a user."""
        user_dir = EXPORT_DIR / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    @staticmethod
    def get_active_export(user_id: str) -> dict | None:
        """Check if there's an existing export for this user."""
        user_dir = EXPORT_DIR / user_id
        if not user_dir.exists():
            return None

        for f in user_dir.glob('export-*.zip'):
            return {
                'filename': f.name,
                'path': f'exports/{user_id}/{f.name}',
                'created_at': f.stat().st_mtime,
                'size': f.stat().st_size,
            }
        return None

    @staticmethod
    def _serialize(obj: Any) -> Any:
        """JSON-safe serialization for model objects."""
        if hasattr(obj, 'model_dump'):
            return obj.model_dump()
        if hasattr(obj, '__dict__'):
            return obj.__dict__
        return obj

    @staticmethod
    def collect_user_data(user_id: str) -> dict:
        """
        Collect all exportable data for a user.
        Returns a dict of data category -> list/dict of records.
        """
        data = {}

        # User profile
        user = Users.get_user_by_id(user_id)
        if user:
            profile = user.model_dump()
            # Remove sensitive fields
            for field in ['settings', 'oauth', 'scim']:
                profile.pop(field, None)
            data['profile'] = profile

            # User settings (without sensitive internals)
            if user.settings:
                settings = user.settings.model_dump() if hasattr(user.settings, 'model_dump') else user.settings
                data['settings'] = settings

        # Chats
        chats = Chats.get_chats_by_user_id(user_id)
        data['chats'] = [ExportService._serialize(c) for c in chats]

        # Memories
        memories = Memories.get_memories_by_user_id(user_id)
        data['memories'] = [ExportService._serialize(m) for m in memories]

        # Notes
        notes = Notes.get_notes_by_user_id(user_id)
        data['notes'] = [ExportService._serialize(n) for n in notes]

        # Prompts
        prompts = Prompts.get_prompts_by_user_id(user_id)
        data['prompts'] = [ExportService._serialize(p) for p in prompts]

        # Tools
        tools = Tools.get_tools_by_user_id(user_id)
        data['tools'] = [ExportService._serialize(t) for t in tools]

        # Custom models
        models = Models.get_models_by_user_id(user_id)
        data['models'] = [ExportService._serialize(m) for m in models]

        # Feedbacks
        feedbacks = Feedbacks.get_feedbacks_by_user_id(user_id)
        data['feedbacks'] = [ExportService._serialize(f) for f in feedbacks]

        # Tags
        tags = Tags.get_tags_by_user_id(user_id)
        data['tags'] = [ExportService._serialize(t) for t in tags]

        # Folders
        folders = Folders.get_folders_by_user_id(user_id)
        data['folders'] = [ExportService._serialize(f) for f in folders]

        # Files metadata
        files = Files.get_files_by_user_id(user_id)
        data['files'] = [ExportService._serialize(f) for f in files]

        # Knowledge bases metadata
        knowledge_bases = Knowledges.get_knowledge_bases_by_user_id(user_id)
        data['knowledge_bases'] = [ExportService._serialize(kb) for kb in knowledge_bases]

        return data

    @staticmethod
    def _get_local_file_ids(data: dict) -> list[str]:
        """
        Determine which files should be included as actual content.
        Only include files from local knowledge bases (not cloud-synced).
        """
        # Build set of cloud-synced KB IDs
        cloud_kb_ids = set()
        for kb in data.get('knowledge_bases', []):
            kb_type = kb.get('type', 'local')
            if kb_type in ('onedrive', 'google_drive'):
                cloud_kb_ids.add(kb.get('id'))

        # Build set of file IDs that belong to cloud KBs
        cloud_file_ids = set()
        for kb in data.get('knowledge_bases', []):
            if kb.get('id') in cloud_kb_ids:
                kb_data = kb.get('data', {})
                if kb_data and isinstance(kb_data, dict):
                    file_ids = kb_data.get('file_ids', [])
                    cloud_file_ids.update(file_ids)

        # Return file IDs that are NOT in cloud KBs
        local_file_ids = []
        for f in data.get('files', []):
            if f.get('id') not in cloud_file_ids:
                local_file_ids.append(f.get('id'))

        return local_file_ids

    @staticmethod
    def build_export_zip(user_id: str, data: dict) -> Path:
        """
        Build a ZIP file containing all user data.
        Returns the path to the generated ZIP.
        """
        export_dir = ExportService.get_export_dir(user_id)

        # Clean up any previous exports for this user
        for old_file in export_dir.glob('export-*.zip'):
            old_file.unlink()

        timestamp = int(time.time())
        zip_path = export_dir / f'export-{timestamp}.zip'

        local_file_ids = ExportService._get_local_file_ids(data)

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Manifest
            manifest = {
                'schema_version': EXPORT_SCHEMA_VERSION,
                'exported_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'user_id': user_id,
                'user_email': data.get('profile', {}).get('email', ''),
                'user_name': data.get('profile', {}).get('name', ''),
                'contents': {
                    'chats': len(data.get('chats', [])),
                    'memories': len(data.get('memories', [])),
                    'notes': len(data.get('notes', [])),
                    'prompts': len(data.get('prompts', [])),
                    'tools': len(data.get('tools', [])),
                    'models': len(data.get('models', [])),
                    'feedbacks': len(data.get('feedbacks', [])),
                    'tags': len(data.get('tags', [])),
                    'folders': len(data.get('folders', [])),
                    'files': len(data.get('files', [])),
                    'files_included': len(local_file_ids),
                    'knowledge_bases': len(data.get('knowledge_bases', [])),
                },
            }
            zf.writestr('manifest.json', json.dumps(manifest, indent=2, default=str))

            # Individual data files
            for key in [
                'profile',
                'settings',
                'chats',
                'memories',
                'notes',
                'prompts',
                'tools',
                'models',
                'feedbacks',
                'tags',
                'folders',
            ]:
                if key in data:
                    zf.writestr(
                        f'{key}.json',
                        json.dumps(data[key], indent=2, default=str, ensure_ascii=False),
                    )

            # Files metadata (always included)
            zf.writestr(
                'files/metadata.json',
                json.dumps(data.get('files', []), indent=2, default=str, ensure_ascii=False),
            )

            # Knowledge bases metadata (always included)
            zf.writestr(
                'knowledge/metadata.json',
                json.dumps(data.get('knowledge_bases', []), indent=2, default=str, ensure_ascii=False),
            )

            # Actual file contents for local (non-cloud) files
            for file_id in local_file_ids:
                try:
                    file_record = Files.get_file_by_id(file_id)
                    if file_record and file_record.path:
                        local_path = Storage.get_file(file_record.path)
                        if local_path and Path(local_path).exists():
                            meta = file_record.meta or {}
                            original_name = meta.get('name', file_record.filename or file_id)
                            zf.write(local_path, f'files/uploads/{file_id}_{original_name}')
                except Exception as e:
                    log.warning(f'Failed to include file {file_id} in export: {e}')

        return zip_path

    @staticmethod
    def generate_export(user_id: str):
        """
        Synchronous entry point for background task.
        Collects data, builds ZIP, notifies user.
        """
        try:
            # Notify: processing. Dispatch onto uvicorn's main loop —
            # asyncio.run would create a throwaway loop and poison the shared
            # Socket.IO Redis pool.
            run_on_main_loop(emit_export_status(user_id, 'processing'))

            # Collect all data
            data = ExportService.collect_user_data(user_id)

            # Build ZIP
            zip_path = ExportService.build_export_zip(user_id, data)

            # Notify: completed
            relative_path = f'exports/{user_id}/{zip_path.name}'
            run_on_main_loop(emit_export_status(user_id, 'completed', export_path=relative_path))

            log.info(f'Data export completed for user {user_id}: {zip_path}')

        except Exception as e:
            log.error(f'Data export failed for user {user_id}: {e}')
            run_on_main_loop(emit_export_status(user_id, 'failed', error=str(e)))

    @staticmethod
    def cleanup_expired_exports():
        """Delete export ZIPs older than DATA_EXPORT_RETENTION_HOURS."""
        if not EXPORT_DIR.exists():
            return {'deleted': 0}

        cutoff = time.time() - (DATA_EXPORT_RETENTION_HOURS * 3600)
        deleted = 0

        for user_dir in EXPORT_DIR.iterdir():
            if not user_dir.is_dir():
                continue
            for zip_file in user_dir.glob('export-*.zip'):
                if zip_file.stat().st_mtime < cutoff:
                    zip_file.unlink()
                    deleted += 1
            # Remove empty user dirs
            if not any(user_dir.iterdir()):
                user_dir.rmdir()

        return {'deleted': deleted}
