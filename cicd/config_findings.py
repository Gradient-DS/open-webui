"""Render the complete observed config-key union from one or more pass artifacts."""

import argparse
import json
from pathlib import Path

BEGIN = '<!-- config-corpus-diff:start -->'
END = '<!-- config-corpus-diff:end -->'
CONSUMERS = {
    'rag.file.max_size': '`routers/files.py:303`, `int(max_size_mb)`; uploads fail with 500.',
    'rag.embedding_engine': '`retrieval/utils.py:1170`, `get_embedding_function`; vector ingestion fails.',
}


def render(paths):
    reached = {}
    verified = []
    for path in paths:
        tally = json.loads(path.read_text())['passes']['seeding']
        verified.append(f'{path.name}: restore verified = {tally["config_restore_verified"]}')
        for item in tally['config_findings']:
            reached.setdefault(item['key'], set()).add(item['route'])
    rows = [
        BEGIN,
        'Live seeding diffs: ' + '; '.join(verified),
        '',
        '| Config key | Routes that changed the value | Consumer / downstream effect |',
        '| --- | --- | --- |',
    ]
    for key, routes in sorted(reached.items()):
        consumer = CONSUMERS.get(key, 'Persistence observed; consumer and downstream failure need live/source review.')
        rows.append(f'| `{key}` | ' + ', '.join(f'`{route}`' for route in sorted(routes)) + f' | {consumer} |')
    rows += [
        '',
        f'Complete observed changed-key union: **{len(reached)}**. '
        'This includes collateral/default writes and transformed payloads; '
        '`corpus_config_keys` separately identifies recognized corpus values.',
        END,
    ]
    return '\n'.join(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifacts', type=Path, nargs='+')
    parser.add_argument('--findings', type=Path)
    args = parser.parse_args()
    report = render(args.artifacts)
    if args.findings:
        document = args.findings.read_text()
        start, end = document.index(BEGIN), document.index(END) + len(END)
        args.findings.write_text(document[:start] + report + document[end:])
    else:
        print(report)


if __name__ == '__main__':
    main()
