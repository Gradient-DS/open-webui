"""The internal retrieval surface retained by collections/documents plan B7."""

import ast
from pathlib import Path


def test_internal_retrieval_surface_retains_query_and_file_routes():
    """B7 retains query and file routes while removing the collection-discovery route."""
    source = Path(__file__).resolve().parents[2] / 'routers' / 'internal_retrieval.py'
    routes = {
        (decorator.func.attr, ast.literal_eval(decorator.args[0]))
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.AsyncFunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == 'router'
    }
    assert routes == {
        ('get', '/accessible-files'),
        ('get', '/knowledge/{knowledge_id}/files'),
        ('get', '/files/{file_id}/content'),
        ('get', '/files/{file_id}/raw'),
        ('post', '/query'),
        ('post', '/files/upload'),
        ('post', '/email-document'),
    }
