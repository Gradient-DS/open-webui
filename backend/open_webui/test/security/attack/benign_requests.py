"""Structural positive-control bodies from OpenAPI; semantic refusals stay visible."""

from copy import deepcopy

from .seeds import SPEC


def value_for(schema, spec, parameters, path, name='', seen=()):
    if '$ref' in schema:
        ref = schema['$ref']
        if ref in seen:
            return None
        node = spec
        for key in ref.removeprefix('#/').split('/'):
            node = node[key]
        return value_for(node, spec, parameters, path, name, (*seen, ref))
    if 'default' in schema:
        return deepcopy(schema['default'])
    if 'enum' in schema:
        return schema['enum'][0]
    for union in ('anyOf', 'oneOf', 'allOf'):
        if union in schema:
            options = [s for s in schema[union] if s.get('type') != 'null']
            return value_for(options[0], spec, parameters, path, name, seen) if options else None
    kind = schema.get('type')
    if kind == 'object' or 'properties' in schema:
        return {
            key: value_for(schema['properties'][key], spec, parameters, path, key, seen)
            for key in schema.get('required', [])
            if key in schema.get('properties', {})
        }
    if kind == 'array':
        return [
            value_for(schema.get('items', {}), spec, parameters, path, name, seen)
            for _ in range(max(1, schema.get('minItems', 0)))
        ]
    if kind == 'boolean':
        return False
    if kind in ('integer', 'number'):
        return max(1, schema.get('minimum', 1))
    if kind == 'null':
        return None
    return parameters.get((path, name), 'attack-control')


def benign_request(route, parameters, spec=SPEC):
    method, path = route.split(' ', 1)
    operation = spec['paths'][path][method.lower()]
    result = {}
    content = operation.get('requestBody', {}).get('content', {})
    if 'application/json' in content:
        result['json'] = value_for(content['application/json'].get('schema', {}), spec, parameters, path)
    elif 'multipart/form-data' in content:
        schema = content['multipart/form-data'].get('schema', {})
        result['data'] = value_for(schema, spec, parameters, path)
        # A file upload must contain a file, even when OpenAPI marks it optional.
        result['files'] = {'file': ('attack-control.txt', b'Attack control.', 'text/plain')}
    params = {}
    for item in [*spec['paths'][path].get('parameters', []), *operation.get('parameters', [])]:
        if item.get('in') == 'query' and item.get('required'):
            params[item['name']] = value_for(item.get('schema', {}), spec, parameters, path, item['name'])
    if params:
        result['params'] = params
    return result
