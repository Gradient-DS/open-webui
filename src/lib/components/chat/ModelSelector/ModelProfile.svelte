<script lang="ts">
	import { getContext } from 'svelte';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import Bolt from '$lib/components/icons/Bolt.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Flag from '$lib/components/icons/Flag.svelte';
	import { PROFILE_AXES, ORIGIN_META, type ModelProfile } from '$lib/utils/models/profile';

	const i18n = getContext('i18n');

	export let profile: ModelProfile = {};
	// Header mode renders the axis icons + labels (the legend); row mode renders the meters.
	export let header = false;

	const ACCENT = '#0A6B8C';

	const ICONS: Record<string, typeof Sparkles> = {
		quality: Sparkles,
		speed: Bolt
	};

	$: origin = profile.origin ? ORIGIN_META[profile.origin] : undefined;

	const levelLabel = (v: number | undefined): string => {
		if (typeof v !== 'number') return $i18n.t('Not rated');
		if (v >= 3) return $i18n.t('High');
		if (v === 2) return $i18n.t('Medium');
		return $i18n.t('Low');
	};
</script>

<!-- Fixed-width cells so the header icons/labels line up above the row meters column-for-column -->
<div class="flex items-center gap-1.5 shrink-0">
	{#each PROFILE_AXES as axis}
		{@const v = profile[axis.key]}
		<div class="w-[42px] min-w-0 flex flex-col items-center justify-center {header ? 'gap-0.5' : ''}">
			{#if header}
				<Tooltip content={`<b>${$i18n.t(axis.labelKey)}</b><br>${$i18n.t(axis.descKey)}`}>
					<svelte:component
						this={ICONS[axis.key]}
						className="size-3 text-gray-600 dark:text-gray-300"
						strokeWidth="2"
					/>
				</Tooltip>
				<span
					class="text-[9px] leading-none font-medium text-gray-600 dark:text-gray-300 whitespace-nowrap"
				>
					{$i18n.t(axis.labelKey)}
				</span>
			{:else if typeof v === 'number'}
				<Tooltip content={`${$i18n.t(axis.labelKey)}: ${v}/3 (${levelLabel(v)})`}>
					<div
						class="flex"
						style="width: 34px; gap: 4px;"
						role="img"
						aria-label={`${$i18n.t(axis.labelKey)}: ${v}/3`}
					>
						{#each [1, 2, 3] as seg}
							<div
								class="flex-1 {seg > v ? 'bg-gray-200 dark:bg-gray-700' : ''}"
								style="height: 6px; border-radius: 2px; background-color: {seg <= v ? ACCENT : ''};"
								aria-hidden="true"
							></div>
						{/each}
					</div>
				</Tooltip>
			{/if}
		</div>
	{/each}

	<!-- Origin flag, in the column where the compliance meter used to sit -->
	<div class="w-[42px] min-w-0 flex flex-col items-center justify-center {header ? 'gap-0.5' : ''}">
		{#if header}
			<Tooltip content={`<b>${$i18n.t('Origin')}</b><br>${$i18n.t('where the model comes from')}`}>
				<GlobeAlt className="size-3 text-gray-600 dark:text-gray-300" strokeWidth="2" />
			</Tooltip>
			<span
				class="text-[9px] leading-none font-medium text-gray-600 dark:text-gray-300 whitespace-nowrap"
			>
				{$i18n.t('Origin')}
			</span>
		{:else if profile.origin && origin}
			<Tooltip content={`${$i18n.t('Origin')}: ${$i18n.t(origin.labelKey)}`}>
				<Flag
					origin={profile.origin}
					className="w-[18px] h-[13px] rounded-[2px]"
					ariaLabel={$i18n.t(origin.labelKey)}
				/>
			</Tooltip>
		{/if}
	</div>
</div>
