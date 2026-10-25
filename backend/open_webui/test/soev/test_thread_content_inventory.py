"""A reviewed inventory of legacy content access, including non-router writers."""

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name('thread_content_inventory.json')
CONTENT_MODELS = {'chats', 'chat_messages', 'shared_chats', 'feedbacks'}


def content_aliases(tree):
    aliases = {'response_streams', 'REDIS_RESPONSE_STREAMS_KEY'}
    aliases.update(
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in {'Chat', 'ChatMessage', 'ChatFile', 'SharedChat', 'Feedback'}
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.removeprefix('open_webui.models.') in CONTENT_MODELS:
                aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name
                    in {
                        'Chat',
                        'Chats',
                        'ChatMessage',
                        'ChatMessages',
                        'ChatFile',
                        'SharedChat',
                        'SharedChats',
                        'Feedback',
                        'Feedbacks',
                    }
                )
    return aliases


def inventory() -> list[str]:
    entries = set()
    for path in ROOT.rglob('*.py'):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {'test', 'migrations'}:
            continue
        tree = ast.parse(path.read_text())
        aliases = content_aliases(tree)
        for definition in tree.body:
            if isinstance(definition, ast.ClassDef) and any(
                isinstance(decorator, ast.Name) and decorator.id == 'legacy_content'
                for decorator in definition.decorator_list
            ):
                entries.update(
                    f'{relative}:{definition.name}.{method.name}:guarded'
                    for method in definition.body
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        if not aliases:
            continue

        def visit(node, owner):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                owner = owner + '.' + node.name
            if isinstance(node, ast.Name) and node.id in aliases:
                entries.add(f'{relative}:{owner.lstrip(".")}:{node.id}')
            for child in ast.iter_child_nodes(node):
                visit(child, owner)

        visit(tree, '')
    return sorted(entries)


def test_every_legacy_content_access_is_in_the_reviewed_inventory():
    expected = json.loads(MANIFEST.read_text())
    actual = inventory()
    assert actual == expected, {
        'unreviewed': sorted(set(actual) - set(expected)),
        'removed': sorted(set(expected) - set(actual)),
    }


def test_v2_has_no_unguarded_local_content_writer():
    # Facades cover all indirect callers. Direct SQL entrypoints are separately
    # inventoried because the organisation repository legitimately uses chat.
    for file, cls in [
        ('chats', 'ChatTable'),
        ('chat_messages', 'ChatMessageTable'),
        ('shared_chats', 'SharedChatsTable'),
        ('feedbacks', 'FeedbackTable'),
    ]:
        model = ast.parse((ROOT / f'models/{file}.py').read_text())
        definition = next(node for node in model.body if isinstance(node, ast.ClassDef) and node.name == cls)
        assert any(isinstance(node, ast.Name) and node.id == 'legacy_content' for node in definition.decorator_list)

    direct = {
        'utils/subagents.py': {'process_pending_internal_messages', 'delegate'},
        'utils/timers.py': {'claim_due_timers', 'cancel_timers_for_chat', 'execute_due_timer', '_set_timer_state'},
        'services/deletion/offboarding_store.py': {'_delete_threads', 'finalize_local', 'delete_resource_dependents'},
        'soev/migrate_uploads.py': {'_referenced'},
        'soev/migrate_models.py': {'rewrite', 'restore'},
    }
    unguarded = []
    for file, names in direct.items():
        tree = ast.parse((ROOT / file).read_text())
        for name in names:
            definition = next(
                node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name
            )
            statements = definition.body
            first = (
                statements[1]
                if isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant)
                else statements[0]
            )
            if ast.unparse(first) != 'refuse_local_content()':
                unguarded.append(f'{file}:{name}')
    assert unguarded == []
    # New direct ORM/SQL content users must be reviewed even if their entrypoint
    # was already in the broader facade-call inventory.
    actual = set()
    for path in ROOT.rglob('*.py'):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {'test', 'migrations', 'models'}:
            continue
        tree = ast.parse(path.read_text())
        aliases = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module in {f'open_webui.models.{model}' for model in CONTENT_MODELS}
            for alias in node.names
            if alias.name in {'Chat', 'ChatMessage', 'ChatFile', 'SharedChat', 'Feedback'}
        }
        if any(
            isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in aliases
            for node in ast.walk(tree)
        ):
            actual.add(str(relative))
    assert actual == set(direct)
    assert 'protect_content_engine' not in (ROOT / 'internal/db.py').read_text()


def test_organization_repository_has_only_organizational_columns():
    from open_webui.soev.thread_organizations import branches, chat

    assert set(chat.c.keys()) == {
        'id',
        'user_id',
        'thread_id',
        'folder_id',
        'pinned',
        'archived',
        'last_read_at',
        'created_at',
        'updated_at',
    }
    assert set(branches.c.keys()) == {'chat_id', 'thread_id'}


def test_no_unreviewed_raw_sql_content_access():
    sql = re.compile(
        r'\b(?:from|join|into|update)\s+["\[]?(?:chat|chat_message|chat_file|shared_chat|feedback)["\]]?(?!\w)', re.I
    )
    actual = []
    for path in ROOT.rglob('*.py'):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {'test', 'migrations', 'models'}:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func).split('.')[-1] in {'table', 'Table'}:
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in {'chat', 'chat_message', 'chat_file', 'shared_chat', 'feedback'}
                ):
                    actual.append(str(relative))
            if isinstance(node, ast.Call) and ast.unparse(node.func).split('.')[-1] in {
                'text',
                'execute',
                'executemany',
                'executescript',
            }:
                if any(
                    isinstance(value, ast.Constant) and isinstance(value.value, str) and sql.search(value.value)
                    for argument in node.args
                    for value in ast.walk(argument)
                ):
                    actual.append(str(relative))
    assert actual == ['soev/thread_organizations.py']
