<script lang="ts">
	import Switch from '$lib/components/common/Switch.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import EllipsisVertical from '$lib/components/icons/EllipsisVertical.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Sortable from 'sortablejs';
	import { getContext, onMount } from 'svelte';
	import { getLanguages } from '$lib/i18n';
	import { toLocalizedObject } from '$lib/utils/localized';
	const i18n = getContext('i18n');

	export let banners = [];

	let sortable = null;
	let bannerListElement = null;
	let languages: { code: string; title: string }[] = [];
	let editLang = $i18n?.language ?? 'en-US';

	const classNames: Record<string, string> = {
		info: 'bg-blue-500/20 text-blue-700 dark:text-blue-200 ',
		success: 'bg-green-500/20 text-green-700 dark:text-green-200',
		warning: 'bg-yellow-500/20 text-yellow-700 dark:text-yellow-200',
		error: 'bg-red-500/20 text-red-700 dark:text-red-200'
	};

	onMount(async () => {
		languages = await getLanguages();
		// Normalize any legacy plain-string content under the editing language,
		// so the textarea always binds against an object.
		banners = banners.map((b) => ({
			...b,
			content: toLocalizedObject(b.content, editLang)
		}));
	});

	const positionChangeHandler = () => {
		const bannerIdOrder = Array.from(bannerListElement.children).map((child) =>
			child.id.replace('banner-item-', '')
		);

		banners = bannerIdOrder.map((id) => {
			const index = banners.findIndex((banner) => banner.id === id);
			return banners[index];
		});
	};

	$: if (banners) {
		init();
	}

	const init = () => {
		if (sortable) {
			sortable.destroy();
		}

		if (bannerListElement) {
			sortable = new Sortable(bannerListElement, {
				animation: 150,
				handle: '.item-handle',
				onUpdate: async (event) => {
					positionChangeHandler();
				}
			});
		}
	};

	const setBannerContent = (idx: number, value: string) => {
		const current = toLocalizedObject(banners[idx].content, editLang);
		if (value && value.length > 0) {
			current[editLang] = value;
		} else {
			delete current[editLang];
		}
		banners[idx] = { ...banners[idx], content: current };
		banners = banners;
	};

	const filledLanguages = (content: unknown): string[] => {
		if (!content || typeof content !== 'object') return [];
		return Object.entries(content as Record<string, string>)
			.filter(([, v]) => typeof v === 'string' && v.length > 0)
			.map(([k]) => k);
	};
</script>

{#if banners?.length > 0}
	<div class="flex items-center gap-2 mt-2 text-xs">
		<span class="text-gray-500 dark:text-gray-400">{$i18n.t('Editing language')}</span>
		<select
			class="rounded-md bg-transparent text-xs outline-hidden pl-1 pr-5 dark:text-gray-300"
			bind:value={editLang}
		>
			{#each languages as language}
				<option value={language.code} class="text-gray-900">{language.title}</option>
			{/each}
		</select>
	</div>
{/if}

<div class=" flex flex-col gap-3 {banners?.length > 0 ? 'mt-2' : ''}" bind:this={bannerListElement}>
	{#each banners as banner, bannerIdx (banner.id)}
		<div class=" flex justify-between items-start -ml-1" id="banner-item-{banner.id}">
			<EllipsisVertical className="size-4 cursor-move item-handle" />

			<div class="flex flex-row flex-1 gap-2 items-start">
				<select
					class="w-fit capitalize rounded-xl text-xs bg-transparent outline-hidden pl-1 pr-5"
					bind:value={banner.type}
					required
				>
					<option value="" disabled hidden class="text-gray-900">{$i18n.t('Type')}</option>
					<option value="info" class="text-gray-900">{$i18n.t('Info')}</option>
					<option value="warning" class="text-gray-900">{$i18n.t('Warning')}</option>
					<option value="error" class="text-gray-900">{$i18n.t('Error')}</option>
					<option value="success" class="text-gray-900">{$i18n.t('Success')}</option>
				</select>

				<div class="flex flex-col flex-1 mr-2">
					<Textarea
						className="text-xs w-full bg-transparent outline-hidden resize-none"
						placeholder={$i18n.t('Content ({{lang}})', { lang: editLang })}
						value={toLocalizedObject(banner.content, editLang)[editLang] ?? ''}
						onInput={(e) => setBannerContent(bannerIdx, e.target.value)}
						maxSize={100}
					/>
					{#if filledLanguages(banner.content).length > 0}
						<div class="flex flex-wrap gap-1 mt-1">
							{#each filledLanguages(banner.content) as code}
								<span
									class="px-1.5 py-0.5 rounded-md text-[10px] uppercase tracking-wide {code ===
									editLang
										? 'bg-gray-200 dark:bg-gray-800 text-gray-700 dark:text-gray-200'
										: 'bg-gray-100 dark:bg-gray-850 text-gray-500 dark:text-gray-400'}"
								>
									{code}
								</span>
							{/each}
						</div>
					{/if}
				</div>

				<div class="relative -left-2">
					<Tooltip content={$i18n.t('Remember Dismissal')} className="flex h-fit items-center">
						<Switch bind:state={banner.dismissible} />
					</Tooltip>
				</div>
			</div>

			<button
				class="pr-3"
				type="button"
				on:click={() => {
					banners.splice(bannerIdx, 1);
					banners = banners;
				}}
			>
				<XMark className={'size-4'} />
			</button>
		</div>
	{/each}
</div>
