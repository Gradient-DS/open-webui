import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import { canRegenerateResponse } from '$lib/utils/features';

const config = { features: { enable_v2_threads: true } };
it('permits v2 regeneration only for the last finished answer while idle', () => {
	for (const last of [true, false])
		for (const done of [true, false])
			for (const idle of [true, false]) {
				expect(canRegenerateResponse(config, {}, last, done, idle)).toBe(last && done && idle);
			}
});
it('hides the v2 button behind a single flag while preserving legacy visibility and permissions', () => {
	const source = readFileSync('src/lib/components/chat/Messages/ResponseMessage.svelte', 'utf8');
	const flag = source.match(/const V2_REGENERATE_BUTTON_ENABLED = (true|false);/)?.[1];
	expect(flag).toBe('false');
	const expression = source.match(
		/\{#if\s+(\(!\$config\?\.features\?\.enable_v2_threads\s*\|\|\s*V2_REGENERATE_BUTTON_ENABLED\)\s*&&\s*canRegenerateResponse\([\s\S]*?\))\}/
	)?.[1];
	expect(expression).toBeTruthy();
	const visible = new Function(
		'$config',
		'$user',
		'isLastMessage',
		'message',
		'allowRegenerate',
		'V2_REGENERATE_BUTTON_ENABLED',
		'canRegenerateResponse',
		`return ${expression};`
	);
	for (const v2 of [true, false])
		for (const user of [
			{},
			{ role: 'admin' },
			{ permissions: { chat: { regenerate_response: false } } }
		])
			for (const last of [true, false])
				for (const done of [true, false])
					for (const idle of [true, false]) {
						const config = { features: { enable_v2_threads: v2 } };
						const permitted = canRegenerateResponse(config, user, last, done, idle);
						expect(
							visible(config, user, last, { done }, idle, flag === 'true', canRegenerateResponse)
						).toBe(!v2 && permitted);
						expect(visible(config, user, last, { done }, idle, true, canRegenerateResponse)).toBe(
							permitted
						);
					}
});
it('respects regenerate permissions, including admin and legacy behavior', () => {
	expect(
		canRegenerateResponse(
			config,
			{ permissions: { chat: { regenerate_response: false } } },
			true,
			true
		)
	).toBe(false);
	expect(
		canRegenerateResponse(
			config,
			{ role: 'admin', permissions: { chat: { regenerate_response: false } } },
			true,
			true
		)
	).toBe(true);
	expect(canRegenerateResponse({ features: {} }, {}, false, true)).toBe(true);
});
it('uses the visibility policy and keeps regenerate-with-prompt hidden in v2', () => {
	const source = readFileSync('src/lib/components/chat/Messages/ResponseMessage.svelte', 'utf8');
	expect(source).toContain(
		'canRegenerateResponse($config, $user, isLastMessage, message.done, allowRegenerate)'
	);
	expect(source).toContain(
		'!$config?.features?.enable_v2_threads && ($settings?.regenerateMenu ?? true)'
	);
});
