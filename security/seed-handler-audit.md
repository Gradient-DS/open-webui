RUN-TAG: owui-phase6b-fix-seeds-and-auth-ambiguity

Audited on 2026-09-08 against the handlers and model methods in this worktree,
with `docker-compose.ci.yaml` as the sealed-stack configuration. All 36 remaining
`via` entries are accounted for below. Paths in the evidence column are relative
to `backend/open_webui/`. Admin setup does not grant permissions to ordinary
attack identities. Resource creation is distinct from authorization to attack a
route using that resource.

The skill 400 has a concrete database failure path: `models/skills.py:Skill.name`
has `unique=True`, while the old seed reused `Attack skill` with every fresh id.
`SkillsTable.insert_new_skill` catches the insert exception and returns `None`;
`routers/skills.py:create_new_skill` translates that into HTTP 400. Both name and
id now include the pass token. The form's `meta` already defaults to `SkillMeta()`;
omitting it does not explain this failure. The offline regression test pins the
unique columns and exercises the seed with the old name already present, then
runs a second resolution. The reviewer must still confirm the live response.

| Seed keys | Handler/model evidence | Actor, gates, body and sealed-stack assessment |
| --- | --- | --- |
| `user` | `routers/auths.py:add_user` | Admin dependency. Fresh valid email and password, name and user role supplied. No signup gate; default group assignment and session minting are local. |
| `folder` | `routers/folders.py:create_folder`, `check_folders_permission` | Now admin for `features.folders`; namespace enables `folders.enable` first. Fresh sibling name, no parent or sharing grants. |
| `model` | `routers/openai.py:get_models` | Now admin to avoid user model filtering producing an empty registry. `openai.enable` defaults true; `openai_index` first checks the configured stub connection. CI explicitly routes OpenAI to the stub, whose `/v1/models` returns an id. No body. |
| `chat` | `routers/chats.py:create_new_chat`, `models/chats.py:insert_new_chat` | Now the admin folder owner: folder ownership is checked even for admins. `chat` and `folder_id` supplied; title, model and history are explicit. No completion or external model call during insertion. |
| `chat_message` | `routers/chats.py:get_chat_by_id` | Now admin owner; reads the actual saved `history.currentId`. No body. |
| `share` | `routers/chats.py:share_chat_by_id` | Now admin for `chat.share`, and the same chat owner because the lookup always includes user id. Returns original chat with the persisted snapshot's `share_id`. No body. |
| `channel` | `routers/channels.py:create_new_channel`, `check_channels_access`; `models/channels.py:insert_new_channel` | Now admin for `features.channels`; namespace enables `channels.enable`. Group type, fresh name and empty invitee list supplied. Model adds the creator as a member. No public grants. |
| `channel_message` | `routers/channels.py:post_new_message`, `new_message_handler` | Now the admin group member. Membership has no admin exemption. Nonempty content and explicit empty `data` supplied; no parent/reply binding, files or model mentions. |
| `channel_webhook` | `routers/channels.py:create_channel_webhook` | Now admin for channel feature and manager checks. Name supplied; profile image omitted. This is an inbound hook with a generated token, and requires no outbound URL. |
| `webhook_token` | `routers/channels.py:get_channel_webhooks` | Now admin for channel feature and manager checks. Depends on hook creation; fresh channel makes `0.token` unambiguous. |
| `knowledge`, `confluence_knowledge`, `google_drive_knowledge`, `onedrive_knowledge` | `routers/knowledge.py:create_new_knowledge` | Now admin for `workspace.knowledge`. `require_feature('knowledge')` uses environment `FEATURE_KNOWLEDGE`, default true and not overridden in CI. Name and description supplied; all four selected types are explicitly allowed. Provider KB creation stores a row without OAuth or provider retrieval. Metadata embedding is scheduled separately and catches failures. |
| `directory` | `routers/knowledge.py:create_knowledge_directory`, `_verify_knowledge_write_access` | Now admin owner of the local KB. Name supplied, parent omitted for a root directory. Rejects external KBs; this dependency is local. |
| `connection` | `routers/knowledge.py:create_external_knowledge_connection`, `_validate_external_connection_form` | Existing admin actor is correct. Nonblank name and endpoint, supported `qdrant` provider and empty auth config supplied. Stores configuration without URL resolution or a Qdrant call. |
| `external_source` | `routers/knowledge.py:create_external_knowledge`, `_normalize_external_source` | Existing admin actor is correct. Existing connection id, name and source name supplied. `source.config.content_field` is mandatory in the handler despite the loose dictionary schema; the seed already supplies `text`. Collection is the default source type. This endpoint stores a mapping without testing Qdrant retrieval; metadata embedding catches errors. |
| `file` | `routers/files.py:upload_file`, `upload_file_handler` | Now admin to avoid the cumulative per-user file count cap across passes. Multipart `file` contains tiny genuine text, matching filename and MIME. `process=false` avoids ingestion; size and sniff guards still execute. Storage goes to configured in-network MinIO. |
| `filename` | `routers/files.py:get_file_by_id` | Now admin file owner. Extracts the saved filename, no fabricated filename or provider request. |
| `attachment` | `routers/files.py:list_file_attachments` | Keeps the caller, matching `seed_integration`'s service-account owner. Requires the real parent file and read access. Integration seeder already rejects ingestion errors and requires one saved attachment before this read. No body. |
| `note` | `routers/notes.py:create_new_note` | Now admin for `features.notes`. Title and content data supplied. No global creation gate or external processing. |
| `calendar` | `routers/calendar.py:create_calendar`, `check_calendar_permission` | Now admin for `features.calendar`; namespace enables `calendar.enable`. Name is required and supplied; color/data/meta/grants may be omitted. |
| `event` | `routers/calendar.py:create_event`, `_check_calendar_access`; `models/calendar.py:CalendarEventForm`, `insert_new_event` | Now admin calendar owner and feature-gate exempt actor. Required calendar id, title and start supplied, with end after start. Corrected start/end to epoch nanoseconds used by calendar range and recurrence logic. No recurrence or attendees needed. |
| `automation` | `routers/automations.py:create_new_automation`, `check_automations_permission`, `check_automation_limits`; `utils/automations.py:validate_rrule` | Now admin for `features.automations` and cumulative count/minimum-interval limits. Namespace enables `automations.enable`. Name, prompt, model id and non-exhausted daily RRULE supplied; inactive prevents scheduled completion calls. |
| `group` | `routers/groups.py:create_new_group` | Existing admin dependency satisfied. Name and description supplied; model returns id and router adds member count. No external services. |
| `function` | `routers/functions.py:create_new_function` | Existing admin dependency satisfied. Fresh identifier, name, content and meta supplied. Content defines a loadable Action with Valves and UserValves; only installed Pydantic is imported. No package installation or network fetch. |
| `tool` | `routers/tools.py:create_new_tools` | Now admin for `workspace.tools` or `workspace.tools_import`. Fresh Python identifier, name, meta and a Tools class with an annotated echo method and valve classes supplied. Handler loads the local content and generates specs. |
| `skill` | `routers/skills.py:create_new_skill`, `models/skills.py:Skill`, `SkillForm`, `insert_new_skill` | Now admin for `workspace.skills` or `workspace.skills_import`. Required id/name/content supplied; **both unique id and name are fresh**. Meta defaults correctly; no skill-file upload, package manager or external host required. |
| `prompt`, `text` | `routers/prompts.py:create_new_prompt`, `models/prompts.py:insert_new_prompt` | Now admin for `workspace.prompts` or `workspace.prompts_import`. Required command/name/content supplied. Commands already have distinct prefixes and pass tokens, satisfying database uniqueness. Insertion also creates the initial history version. |
| `prompt_history` | `routers/prompts.py:get_prompt_by_id` | Now admin prompt owner; reads its actual `version_id`. No body. |
| `memory` | `routers/memories.py:add_memory`, `check_memories_permission`, `AddMemoryForm` | Now admin for `features.memories`; namespace enables `memories.enable` before insertion. Nonblank content supplied; type defaults to context and path may be absent. Unlike knowledge metadata, embedding and vector failures propagate: CI routes embeddings to the stub and provides real Weaviate. Must confirm live. |
| `feedback` | `routers/evaluations.py:create_feedback`, `models/feedbacks.py:insert_new_feedback` | Now the admin chat owner for consistency. Verified-user dependency, no feature gate. Type/rating/model and chat metadata supplied; snapshot is optional and insertion accepts it absent. |
| `archive` | `routers/archives.py:create_user_archive`, `services/archival/service.py:ArchiveService.create_archive` | Existing admin actor and namespace dependency satisfy `admin.enable_user_archival`. Fresh disposable user exists; reason supplied. Empty chat list is permitted. Archive service persists the profile/chats record; it does not delete the target or require external storage. |
| `invite`, `invite_token` | `routers/invites.py:create_invite` | Existing admin actors are correct. Separate fresh valid emails avoid user/pending-invite collisions. Name and role supplied. `send_email=false` prevents external mail. Expiry uses configured default; response exposes id and raw token. |

Supporting changes keep the task Pipe completion and task lookup under the admin
who now owns the chat. The Pipe itself is already created, activated and checked
by admin. Live resolution tests now call `resolve_parameters(user, admin=admin)`
so they exercise actor selection; previously using only admin concealed mistakes.
The namespace seed adds only `folders.enable` and `memories.enable` to its existing
creation gates. It does not modify `user.permissions` or any SSRF/upload guard.

The reviewer's `event_webhook` conversion and full reason are preserved verbatim
in `attack-surface.toml`. No other entry was converted to `unseedable` in this
repair. External provider authentication remains a prerequisite for later sync or
retrieval operations, but those operations are not called by the provider KB seeds.

Offline verification: attack unit tests, JSON body required-field checks against
the committed OpenAPI, skill uniqueness regression, actor/ownership dependency
checks, source inspection, formatting and lint. No app HTTP request was made.
All 36 `via` seeds still require live resolution to establish actual HTTP success,
including SQL persistence, function/tool loading, event publication, MinIO writes
and embedding/vector calls. Custom seeders (configuration, integration/attachments,
agent and upstream selectors, terminal, export and live task) likewise have no new
live verification from this run. The sealed network's DNS/SSRF behavior is the
reviewer's established finding, not a newly executed network test here.
