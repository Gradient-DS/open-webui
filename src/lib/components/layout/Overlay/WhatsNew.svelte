<script lang="ts">
	// [Gradient] Once-per-release tour of what is new, limited to what this tenant and user can use.
	import { getContext } from 'svelte';
	import { fade, fly } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import { config, models, settings, user } from '$lib/stores';
	import { updateUserSettings } from '$lib/apis/users';
	import {
		CURRENT_RELEASE as release,
		autoModeTools,
		newModels,
		whatsNewSlides,
		type WhatsNewSlide
	} from '$lib/utils/whatsNew';

	import Modal from '$lib/components/common/Modal.svelte';
	import SparklesSolid from '$lib/components/icons/SparklesSolid.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import AutoModeScene from './WhatsNew/AutoModeScene.svelte';
	import CitationScene from './WhatsNew/CitationScene.svelte';
	import ModelsScene from './WhatsNew/ModelsScene.svelte';
	import OutlookScene from './WhatsNew/OutlookScene.svelte';
	import OneDriveScene from './WhatsNew/OneDriveScene.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let { show = $bindable(false) }: { show: boolean } = $props();

	const auto = $derived(autoModeTools($config?.features, $user));
	const fresh = $derived(newModels(release, $models, $config?.features?.whats_new_models));
	const slides = $derived(
		whatsNewSlides(release, { features: $config?.features, user: $user, models: fresh })
	);

	let index = $state(0);
	let direction = $state(1);
	const slide = $derived(slides[index]);
	const last = $derived(index === slides.length - 1);

	const autoText = $derived(
		auto.webSearch && auto.documentWriter
			? {
					title: 'Auto mode for web search and documents',
					body: 'Turn on web search and the document writer in the + menu. soev.ai then decides per question whether to search the web or write a document.'
				}
			: auto.webSearch
				? {
						title: 'Auto mode for web search',
						body: 'Turn on web search in the + menu. soev.ai then decides per question whether to search the web.'
					}
				: {
						title: 'Auto mode for the document writer',
						body: 'Turn on the document writer in the + menu. soev.ai then writes a document as soon as you ask for one.'
					}
	);

	const text: Record<WhatsNewSlide, { title: string; body: string }> = $derived({
		auto_mode: autoText,
		citations: {
			title: 'See exactly where an answer comes from',
			body: 'Click a source number and the PDF opens in the new side panel, on the right page, with the passage it used highlighted.'
		},
		models: {
			title: 'New models',
			body: 'Pick one in the model selector in the chatbox. Which models you see depends on what your organisation has arranged.'
		},
		outlook: {
			title: 'Search your Outlook mail live',
			body: 'Ask about your own mail and soev.ai searches your mailbox right away, with your own Microsoft permissions. Turn it on under Mail search in the + menu.'
		},
		onedrive: {
			title: 'Search your OneDrive live',
			body: 'soev.ai finds, browses and reads documents in your own OneDrive, without a knowledge base. Turn it on under OneDrive search in the + menu.'
		}
	});

	const go = (to: number) => {
		if (to < 0 || to >= slides.length || to === index) return;
		direction = to > index ? 1 : -1;
		index = to;
	};

	const markSeen = async () => {
		settings.set({ ...$settings, whatsNewSeen: release.version });
		await updateUserSettings(localStorage.token, { ui: $settings }).catch(() => {});
	};

	// Esc and the backdrop close the Modal by flipping show; every way out counts as seen.
	let opened = false;
	$effect(() => {
		if (show) opened = true;
		else if (opened) {
			opened = false;
			markSeen();
		}
	});

	// The Modal's focus trap lands on the close button; start on the primary action instead.
	let primary: HTMLButtonElement | undefined = $state();
	$effect(() => {
		if (show && primary) requestAnimationFrame(() => primary?.focus());
	});

	const onKeydown = (event: KeyboardEvent) => {
		if (!show) return;
		if (event.key === 'ArrowRight') go(index + 1);
		if (event.key === 'ArrowLeft') go(index - 1);
	};
</script>

<svelte:window onkeydown={onKeydown} />

<Modal bind:show size="sm" className="bg-white dark:bg-gray-900 rounded-4xl overflow-hidden">
	<div class="relative" aria-roledescription="carousel" aria-label={$i18n.t("What's new")}>
		<div
			class="stage relative h-60 flex items-center justify-center overflow-hidden bg-gradient-to-b from-sky-50 to-white dark:from-sky-950/40 dark:to-gray-900 px-6"
		>
			<div class="glow" aria-hidden="true"></div>
			{#key slide}
				<div
					class="absolute inset-0 flex items-center justify-center px-6"
					in:fly={{ x: 60 * direction, duration: 350, delay: 120, easing: cubicOut }}
					out:fly={{ x: -60 * direction, duration: 200 }}
				>
					{#if slide === 'auto_mode'}
						<AutoModeScene webSearch={auto.webSearch} documentWriter={auto.documentWriter} />
					{:else if slide === 'citations'}
						<CitationScene />
					{:else if slide === 'models'}
						<ModelsScene models={fresh} />
					{:else if slide === 'outlook'}
						<OutlookScene />
					{:else if slide === 'onedrive'}
						<OneDriveScene />
					{/if}
				</div>
			{/key}

			<button
				class="absolute top-3 right-3 p-1.5 rounded-full text-gray-500 hover:bg-black/5 dark:hover:bg-white/10"
				aria-label={$i18n.t('Close')}
				onclick={() => (show = false)}
			>
				<XMark className="size-4" />
			</button>
		</div>

		<div class="px-6 pt-5 pb-5">
			<div
				class="flex items-center gap-1.5 text-xs font-medium text-sky-600 dark:text-sky-400 mb-1.5"
			>
				<SparklesSolid className="size-3.5" />
				{$i18n.t('New in soev.ai {{version}}', { version: release.version })}
			</div>

			{#key slide}
				<div class="min-h-28" in:fade={{ duration: 250, delay: 150 }}>
					<h2 class="text-lg font-semibold text-gray-900 dark:text-white" aria-live="polite">
						{$i18n.t(text[slide].title)}
					</h2>
					<p class="mt-1.5 text-sm leading-relaxed text-gray-600 dark:text-gray-300">
						{$i18n.t(text[slide].body)}
					</p>
				</div>
			{/key}

			<div class="mt-4 flex items-center gap-3">
				{#if slides.length > 1}
					<div class="flex items-center gap-1.5">
						{#each slides as s, i (s)}
							<button
								class="h-1.5 rounded-full transition-all duration-300 {i === index
									? 'w-5 bg-sky-500'
									: 'w-1.5 bg-gray-300 dark:bg-gray-700 hover:bg-gray-400'}"
								aria-label={$i18n.t('Go to slide {{number}}', { number: i + 1 })}
								aria-current={i === index}
								onclick={() => go(i)}
							></button>
						{/each}
					</div>
				{/if}

				<div class="ml-auto flex items-center gap-2">
					{#if last}
						<a
							class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 underline-offset-2 hover:underline"
							href={release.notesUrl}
							target="_blank"
							rel="noopener noreferrer"
							onclick={() => (show = false)}
						>
							{$i18n.t('Read the release notes')}
						</a>
					{:else}
						<button
							class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 px-2 py-1.5"
							onclick={() => (show = false)}
						>
							{$i18n.t('Skip')}
						</button>
					{/if}
					{#if index > 0}
						<button
							class="text-sm px-3.5 py-1.5 rounded-full border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-200"
							onclick={() => go(index - 1)}
						>
							{$i18n.t('Back')}
						</button>
					{/if}
					<button
						class="text-sm font-medium px-4 py-1.5 rounded-full bg-gray-900 hover:bg-gray-800 text-white dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100"
						bind:this={primary}
						onclick={() => (last ? (show = false) : go(index + 1))}
					>
						{last ? $i18n.t('Got it') : $i18n.t('Next')}
					</button>
				</div>
			</div>
		</div>
	</div>
</Modal>

<style>
	.glow {
		position: absolute;
		width: 18rem;
		height: 18rem;
		border-radius: 9999px;
		background: radial-gradient(circle, rgb(56 189 248 / 0.18), transparent 70%);
		animation: drift 8s ease-in-out infinite alternate;
	}
	@keyframes drift {
		from {
			transform: translate(-30%, -10%);
		}
		to {
			transform: translate(30%, 10%);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.glow {
			animation: none;
		}
	}
</style>
