import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import { createInstance } from 'i18next';
import en from '$lib/i18n/locales/en-US/translation.json';
import nl from '$lib/i18n/locales/nl-NL/translation.json';
import { turnErrorDetails } from '$lib/utils/turnError';
import TurnErrorDetails from './TurnErrorDetails.svelte';

const traceId = '1234567890abcdef1234567890abcdef';
const error = {
	code: 'upstream_error',
	status: 502,
	upstream_status: 500,
	trace_id: traceId,
	message: 'Fictional private message',
	detail: 'Fictional upstream body with cedar-secret.pdf',
	prompt: 'Fictional private prompt'
};

const show = async (value: unknown, language = 'en-US') => {
	const i18n = createInstance();
	await i18n.init({
		lng: language,
		returnEmptyString: false,
		keySeparator: false,
		nsSeparator: false,
		resources: { 'en-US': { translation: en }, 'nl-NL': { translation: nl } }
	});
	return render(TurnErrorDetails, {
		props: { error: value },
		context: new Map([['i18n', writable(i18n)]])
	}).body;
};

it.each([
	['en-US', ['Platform error code', 'HTTP status', 'Upstream status', 'Trace ID', 'Copy trace ID']],
	['nl-NL', ['Platformfoutcode', 'HTTP-status', 'Upstreamstatus', 'Trace-ID', 'Trace-ID kopiëren']]
])('renders only identifiers and codes in %s', async (language, labels) => {
	const html = await show(error, language as string);
	for (const label of labels) expect(html).toContain(label);
	for (const value of ['upstream_error', '502', '500', traceId]) expect(html).toContain(value);
	for (const value of [error.message, error.detail, error.prompt, 'cedar-secret.pdf']) {
		expect(html).not.toContain(value);
	}
	expect(html).not.toContain('<details open');
});

it('omits unavailable upstream and trace fields and the copy button', async () => {
	const html = await show({ code: 'failed', status: 502 });
	expect(html).toContain('failed');
	expect(html).toContain('502');
	for (const value of ['Upstream status', 'Trace ID', '<button', 'undefined', 'null']) {
		expect(html).not.toContain(value);
	}
});

it.each([
	true,
	null,
	{ content: JSON.stringify(error) },
	{
		code: 'Fictional private text.pdf',
		status: '502 Fictional private text',
		upstream_status: 502.5,
		trace_id: 'Fictional private text'
	}
])('does not parse strings or render invalid diagnostic fields: %j', async (value) => {
	expect(turnErrorDetails(value)).toEqual({});
	expect(await show(value)).not.toContain('<details');
});

it('copies only the trace id through the disclosure button handler', async () => {
	const component = readFileSync(
		'src/lib/components/chat/Messages/TurnErrorDetails.svelte',
		'utf8'
	);
	const script = component.split('</script>')[0].split('<script lang="ts">')[1];
	const source = ts.createSourceFile('Details.ts', script, ts.ScriptTarget.Latest);
	let expression: ts.Expression | undefined;
	const visit = (node: ts.Node) => {
		if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'copyTraceId') {
			expression = node.initializer;
		}
		ts.forEachChild(node, visit);
	};
	visit(source);
	if (!expression) throw new Error('Missing copy handler');
	const js = ts.transpileModule(`const handler = ${expression.getText(source)};`, {
		compilerOptions: { target: ts.ScriptTarget.ESNext }
	}).outputText;
	const copy = vi.fn().mockResolvedValue(true);
	const handler = new Function(
		'details',
		'copyToClipboard',
		`let copied = false; ${js}; return handler;`
	)(turnErrorDetails(error), copy);
	await handler();
	expect(copy).toHaveBeenCalledExactlyOnceWith(traceId);
	expect(component).toContain('onclick={copyTraceId}');
});
