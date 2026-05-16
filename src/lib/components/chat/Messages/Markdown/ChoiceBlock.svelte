<script lang="ts">
	import { onDestroy, getContext } from 'svelte';
	import { submitPromptSignal, choiceBlockRegistry } from '$lib/stores';
	import type { ChoiceRegistration } from '$lib/stores';
	import type { ChoiceProps } from '$lib/types/present_ui';
	import Checkbox from '$lib/components/common/Checkbox.svelte';

	const i18n: any = getContext('i18n');

	// Typed props arrive validated from the agent service. The
	// PresentUIDispatcher passes ``props`` straight through — no JSON
	// parsing, no parseError fallback, no raw-payload-as-text leak.
	// ChoiceProps is auto-generated from the Pydantic schema (see
	// `npm run generate:ui-schemas`).

	export let props: ChoiceProps;
	export let blockId: string = '';
	export let messageId: string = '';
	export let messageDone: boolean = false;

	$: payload = props;
	$: messageScope = messageId;

	// Local UI state. For multi=false, `selection` is the picked label (or null).
	// For multi=true, `multiSelected` holds ticked options and `selection` is comma-joined.
	// `freeText` holds an inline-typed answer when allow_freetext is on; it's mutually
	// exclusive with `multiSelected`/clicked button — see togglePickMulti, pick, onFreeTextInput.
	let selection: string | null = null;
	let multiSelected: Set<string> = new Set();
	let freeText = '';
	let answered = false;
	let restoredFromStorage = false;
	let registered = false;

	$: allowFreetext = payload?.allow_freetext !== false;

	$: storageKey = payload ? `ui:choice:${messageScope}:${payload.id}` : null;
	$: registryKey = payload?.id ?? null;

	// Restore persisted answered state once payload is known.
	$: if (payload && storageKey && !restoredFromStorage) {
		try {
			const stored = localStorage.getItem(storageKey);
			if (stored) {
				selection = stored;
				answered = true;
				if (registered) writeRegistry({ selection: stored, answered: true });
			}
		} catch {}
		restoredFromStorage = true;
	}

	const writeRegistry = (patch: Partial<ChoiceRegistration>) => {
		if (!registryKey || !messageScope) return;
		const key = registryKey;
		choiceBlockRegistry.update((map) => {
			const scope = { ...(map[messageScope] ?? {}) };
			const prev: ChoiceRegistration =
				scope[key] ?? {
					field: payload?.field,
					question: payload?.question,
					selection: null,
					answered: false
				};
			scope[key] = { ...prev, ...patch };
			return { ...map, [messageScope]: scope };
		});
	};

	// Register self once when payload is first known. We deliberately do NOT
	// read `selection`/`answered` here — those are written by the localStorage
	// restore block and by handlers, which call writeRegistry directly. Reading
	// them here would create a static reactive cycle with the mirror block below.
	$: if (payload && registryKey && messageScope && !registered) {
		registered = true;
		writeRegistry({
			field: payload.field,
			question: payload.question,
			selection: null,
			answered: false
		});
	}

	onDestroy(() => {
		if (!registered || !registryKey || !messageScope) return;
		choiceBlockRegistry.update((map) => {
			const scope = { ...(map[messageScope] ?? {}) };
			delete scope[registryKey];
			if (Object.keys(scope).length === 0) {
				const next = { ...map };
				delete next[messageScope];
				return next;
			}
			return { ...map, [messageScope]: scope };
		});
	});

	// Reactive view of siblings (everything in this scope except us).
	$: scopeEntries = $choiceBlockRegistry[messageScope] ?? {};
	$: siblingIds = Object.keys(scopeEntries).filter((bid) => bid !== registryKey);
	$: hasSiblings = siblingIds.length > 0;
	$: allHaveSelections =
		!!selection &&
		siblingIds.every((bid) => {
			const entry = scopeEntries[bid];
			return !!entry?.selection;
		});

	// Single-block click: lock & submit immediately. Multi-block click: just lock.
	const pick = (label: string) => {
		if (answered || !messageDone) return;
		freeText = '';
		selection = label;
		writeRegistry({ selection: label });
		if (!hasSiblings && !payload?.multi) {
			confirmAll();
		}
	};

	const togglePickMulti = (opt: string) => {
		if (answered || !messageDone) return;
		freeText = '';
		const next = new Set(multiSelected);
		if (next.has(opt)) {
			next.delete(opt);
		} else {
			next.add(opt);
		}
		multiSelected = next;
		if (payload?.multi) {
			const ordered = (payload.options ?? []).filter((o) => multiSelected.has(o));
			selection = ordered.length ? ordered.join(', ') : null;
			writeRegistry({ selection });
		}
	};

	const onFreeTextInput = () => {
		if (answered || !messageDone) return;
		const trimmed = freeText.trim();
		if (trimmed.length > 0) {
			multiSelected = new Set();
			selection = trimmed;
			writeRegistry({ selection: trimmed });
		} else {
			selection = null;
			writeRegistry({ selection: null });
		}
	};

	const submitFreeText = () => {
		if (answered || !messageDone) return;
		const trimmed = freeText.trim();
		if (!trimmed) return;
		pick(trimmed);
	};

	const onFreeTextKeydown = (e: KeyboardEvent) => {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			submitFreeText();
		}
	};

	const confirmAll = () => {
		if (answered || !messageDone || !payload) return;
		if (!selection) return;
		if (hasSiblings && !allHaveSelections) return;

		// Snapshot the registry so we can build a deterministic combined payload.
		const scope = { ...($choiceBlockRegistry[messageScope] ?? {}) };
		// Ensure our latest selection is in the snapshot (in case the reactive sync is pending).
		if (registryKey) {
			scope[registryKey] = {
				...(scope[registryKey] ?? { field: payload.field, question: payload.question, selection: null, answered: false }),
				selection,
				answered: false
			};
		}

		const blockIds = Object.keys(scope);
		const lines = blockIds.map((bid) => {
			const entry = scope[bid];
			const value = entry?.selection ?? '';
			if (entry?.field) return `${entry.field}: ${value}`;
			return value;
		});
		const combined = lines.join('\n');

		// Persist answered state for every block in this scope.
		blockIds.forEach((bid) => {
			const entry = scope[bid];
			if (!entry?.selection) return;
			try {
				localStorage.setItem(`ui:choice:${messageScope}:${bid}`, entry.selection);
			} catch {}
		});

		// Mark all blocks in scope as answered in the registry. Each block's
		// reactive subscription will then flip its local `answered` and disable UI.
		choiceBlockRegistry.update((map) => {
			const next = { ...(map[messageScope] ?? {}) };
			Object.keys(next).forEach((bid) => {
				const e = next[bid];
				if (e?.selection) next[bid] = { ...e, answered: true };
			});
			return { ...map, [messageScope]: next };
		});

		// Reflect locally as well (the reactive sync above only writes when values
		// differ; this guarantees our own UI updates even in edge cases).
		answered = true;

		submitPromptSignal.set({ text: combined, ts: Date.now() });
	};

	// Mirror registry-driven `answered` flips back into local state (so when one
	// block's confirm marks all siblings answered, every block's UI disables).
	$: if (registered && registryKey) {
		const entry = scopeEntries[registryKey];
		if (entry?.answered && !answered) {
			answered = true;
			if (entry.selection && selection !== entry.selection) {
				selection = entry.selection;
			}
		}
	}
</script>

{#if payload}
	<div
		class="my-2 rounded-2xl border border-gray-100 dark:border-gray-800 px-4 py-3"
		data-ui-choice-id={payload.id}
		data-block-id={blockId}
	>
		{#if payload.question}
			<p class="text-sm mb-2 text-gray-800 dark:text-gray-200">{payload.question}</p>
		{/if}

		{#if payload.multi}
			<div class="flex flex-col gap-1.5 mb-3">
				{#each payload.options as opt (opt)}
					{@const rowDisabled = !!answered || !messageDone}
					<div
						role="button"
						tabindex={rowDisabled ? -1 : 0}
						aria-disabled={rowDisabled}
						aria-pressed={multiSelected.has(opt)}
						class="flex items-center gap-2 text-sm text-left px-1 py-1 rounded-md transition
							{rowDisabled
								? 'opacity-50 cursor-not-allowed'
								: 'cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800'}"
						on:click={() => togglePickMulti(opt)}
						on:keydown={(e) => {
							if (rowDisabled) return;
							if (e.key === 'Enter' || e.key === ' ') {
								e.preventDefault();
								togglePickMulti(opt);
							}
						}}
					>
						<Checkbox
							state={multiSelected.has(opt) ? 'checked' : 'unchecked'}
							disabled={rowDisabled}
						/>
						<span>{opt}</span>
					</div>
				{/each}
			</div>
		{:else}
			<div class="flex flex-wrap gap-1.5">
				{#each payload.options as opt (opt)}
					<button
						type="button"
						class="px-3 py-1.5 rounded-xl text-sm border border-gray-200 dark:border-gray-700
							hover:bg-gray-50 dark:hover:bg-gray-800 transition
							disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent
							{selection === opt ? 'bg-gray-100 dark:bg-gray-800 font-medium' : ''}"
						disabled={!!answered || !messageDone}
						on:click={() => pick(opt)}
					>
						{opt}
					</button>
				{/each}
			</div>
		{/if}

		{#if payload.skip_label}
			{@const skipLabel = payload.skip_label}
			<button
				type="button"
				class="mt-2 text-xs underline opacity-70 hover:opacity-100 transition
					disabled:opacity-30 disabled:cursor-not-allowed"
				disabled={!!answered || !messageDone}
				on:click={() => pick(skipLabel)}
			>
				{skipLabel}
			</button>
		{/if}

		{#if allowFreetext && !answered}
			<div class="mt-3 flex items-center gap-2">
				<input
					type="text"
					bind:value={freeText}
					on:input={onFreeTextInput}
					on:keydown={onFreeTextKeydown}
					disabled={!messageDone}
					placeholder={$i18n?.t
						? $i18n.t('Or type your own answer…')
						: 'Or type your own answer…'}
					class="flex-1 min-w-0 px-3 py-1.5 rounded-xl text-sm
						bg-transparent border border-gray-200 dark:border-gray-700
						focus:outline-none focus:border-gray-400 dark:focus:border-gray-500
						disabled:opacity-50 disabled:cursor-not-allowed"
				/>
				{#if !hasSiblings && !payload.multi}
					<button
						type="button"
						class="px-3 py-1.5 rounded-xl text-sm bg-black text-white dark:bg-white dark:text-black
							transition hover:opacity-90
							disabled:opacity-40 disabled:cursor-not-allowed"
						disabled={!messageDone || freeText.trim().length === 0}
						on:click={submitFreeText}
					>
						{$i18n?.t ? $i18n.t('Send') : 'Send'}
					</button>
				{/if}
			</div>
		{/if}

		{#if payload.multi && !hasSiblings && !answered}
			<!--
				Per-block Confirm only fires for the single-question
				multi-select case (checkboxes within one question). In
				a multi-block batch (``hasSiblings``) the dispatcher
				renders a single shared Confirm at the message level.
			-->
			<div class="mt-3 flex items-center gap-2">
				<button
					type="button"
					class="px-3 py-1.5 rounded-xl text-sm bg-black text-white dark:bg-white dark:text-black
						transition hover:opacity-90
						disabled:opacity-40 disabled:cursor-not-allowed"
					disabled={!messageDone || !selection}
					on:click={confirmAll}
				>
					{$i18n?.t ? $i18n.t('Confirm') : 'Confirm'}
				</button>
			</div>
		{/if}

		{#if answered && selection}
			<p class="mt-2 text-xs text-gray-500 dark:text-gray-400">→ {selection}</p>
		{/if}
	</div>
{/if}
