<script lang="ts">
	import CodeBracket from '$lib/components/icons/CodeBracket.svelte';
	import BarsArrowUp from '$lib/components/icons/BarsArrowUp.svelte';
	import Computer from '$lib/components/icons/Computer.svelte';
	import Database from '$lib/components/icons/Database.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Link from '$lib/components/icons/Link.svelte';
	import Lock from '$lib/components/icons/Lock.svelte';
	import Photo from '$lib/components/icons/Photo.svelte';
	import QueueList from '$lib/components/icons/QueueList.svelte';
	import SettingsAlt from '$lib/components/icons/SettingsAlt.svelte';
	import SoundHigh from '$lib/components/icons/SoundHigh.svelte';
	import UserCircle from '$lib/components/icons/UserCircle.svelte';
	import WrenchAlt from '$lib/components/icons/WrenchAlt.svelte';

	export let id: string;
	export let className = 'size-3.5';
	export let strokeWidth = '2';
</script>

<!-- [Gradient] Icons for the migrated fork admin tabs. -->
{#if id === 'general'}
	<SettingsAlt {className} {strokeWidth} />
{:else if id === 'authentication'}
	<Lock {className} {strokeWidth} />
{:else if id === 'connections'}
	<Link {className} {strokeWidth} />
{:else if id === 'models'}
	<svg
		aria-hidden="true"
		xmlns="http://www.w3.org/2000/svg"
		fill="none"
		viewBox="0 0 24 24"
		stroke="currentColor"
		stroke-width={strokeWidth}
		class={className}
	>
		<path
			stroke-linecap="round"
			stroke-linejoin="round"
			d="M4 7.5C4 5.57 7.58 4 12 4s8 1.57 8 3.5S16.42 11 12 11 4 9.43 4 7.5Z"
		/>
		<path
			stroke-linecap="round"
			stroke-linejoin="round"
			d="M4 7.5v4C4 13.43 7.58 15 12 15s8-1.57 8-3.5v-4"
		/>
		<path
			stroke-linecap="round"
			stroke-linejoin="round"
			d="M4 11.5v4C4 17.43 7.58 19 12 19s8-1.57 8-3.5v-4"
		/>
	</svg>
{:else if id === 'subagents'}
	<UserCircle {className} {strokeWidth} />
{:else if id === 'evaluations'}
	<svg
		aria-hidden="true"
		xmlns="http://www.w3.org/2000/svg"
		fill="none"
		viewBox="0 0 24 24"
		stroke="currentColor"
		stroke-width={strokeWidth}
		class={className}
	>
		<path stroke-linecap="round" stroke-linejoin="round" d="M5 20V10" />
		<path stroke-linecap="round" stroke-linejoin="round" d="M12 20V4" />
		<path stroke-linecap="round" stroke-linejoin="round" d="M19 20v-7" />
	</svg>
{:else if id === 'analytics'}
	<BarsArrowUp {className} {strokeWidth} />
{:else if id === 'integrations'}
	<WrenchAlt {className} {strokeWidth} />
{:else if id === 'documents'}
	<Document {className} {strokeWidth} />
{:else if id === 'web'}
	<GlobeAlt {className} {strokeWidth} />
{:else if id === 'code-execution'}
	<CodeBracket {className} {strokeWidth} />
{:else if id === 'interface'}
	<Computer {className} {strokeWidth} />
{:else if id === 'audio'}
	<SoundHigh {className} {strokeWidth} />
{:else if id === 'images'}
	<Photo {className} {strokeWidth} />
{:else if id === 'pipelines'}
	<QueueList {className} {strokeWidth} />
{:else if id === 'cloud-sync'}
	<svg
		aria-hidden="true"
		xmlns="http://www.w3.org/2000/svg"
		viewBox="0 0 24 24"
		fill="currentColor"
		class={className}
	>
		<path
			fill-rule="evenodd"
			d="M4.755 10.059a7.5 7.5 0 0 1 12.548-3.364l1.903 1.903h-3.183a.75.75 0 1 0 0 1.5h4.992a.75.75 0 0 0 .75-.75V4.356a.75.75 0 0 0-1.5 0v3.18l-1.9-1.9A9 9 0 0 0 3.306 9.67a.75.75 0 1 0 1.45.388Zm15.408 3.352a.75.75 0 0 0-.919.53 7.5 7.5 0 0 1-12.548 3.364l-1.902-1.903h3.183a.75.75 0 0 0 0-1.5H2.984a.75.75 0 0 0-.75.75v4.992a.75.75 0 0 0 1.5 0v-3.18l1.9 1.9a9 9 0 0 0 15.059-4.035.75.75 0 0 0-.53-.918Z"
			clip-rule="evenodd"
		/>
	</svg>
{:else if id === 'email'}
	<svg
		aria-hidden="true"
		xmlns="http://www.w3.org/2000/svg"
		viewBox="0 0 16 16"
		fill="currentColor"
		class={className}
	>
		<path
			d="M2.5 3A1.5 1.5 0 0 0 1 4.5v.793c.026.009.051.02.076.032L7.674 8.51c.206.1.446.1.652 0l6.598-3.185A.755.755 0 0 1 15 5.293V4.5A1.5 1.5 0 0 0 13.5 3h-11Z"
		/>
		<path
			d="M15 6.954 8.978 9.86a2.25 2.25 0 0 1-1.956 0L1 6.954V11.5A1.5 1.5 0 0 0 2.5 13h11a1.5 1.5 0 0 0 1.5-1.5V6.954Z"
		/>
	</svg>
{:else if id === 'security'}
	<svg
		aria-hidden="true"
		xmlns="http://www.w3.org/2000/svg"
		viewBox="0 0 16 16"
		fill="currentColor"
		class={className}
	>
		<path
			fill-rule="evenodd"
			d="M8 1a3.5 3.5 0 0 0-3.5 3.5V7A1.5 1.5 0 0 0 3 8.5v5A1.5 1.5 0 0 0 4.5 15h7a1.5 1.5 0 0 0 1.5-1.5v-5A1.5 1.5 0 0 0 11.5 7V4.5A3.5 3.5 0 0 0 8 1Zm2 6V4.5a2 2 0 1 0-4 0V7h4Z"
			clip-rule="evenodd"
		/>
	</svg>
{:else if id === 'acceptance'}
	<svg
		aria-hidden="true"
		xmlns="http://www.w3.org/2000/svg"
		viewBox="0 0 16 16"
		fill="currentColor"
		class={className}
	>
		<path
			fill-rule="evenodd"
			d="M11.986 3H12a2 2 0 0 1 2 2v6a2 2 0 0 1-1.5 1.937V7A2.5 2.5 0 0 0 10 4.5H4.063A2 2 0 0 1 6 3h.014A2.25 2.25 0 0 1 8.25 1h1.5a2.25 2.25 0 0 1 2.236 2ZM10.5 4v-.75a.75.75 0 0 0-.75-.75h-1.5a.75.75 0 0 0-.75.75V4h3Z"
			clip-rule="evenodd"
		/>
		<path
			fill-rule="evenodd"
			d="M2 7a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V7Zm6.585 1.08a.75.75 0 0 1 .336 1.005l-1.75 3.5a.75.75 0 0 1-1.16.234l-1.75-1.5a.75.75 0 0 1 .977-1.139l1.02.875 1.321-2.64a.75.75 0 0 1 1.006-.336Z"
			clip-rule="evenodd"
		/>
	</svg>
{:else if id === 'external-agents'}
	<svg
		aria-hidden="true"
		xmlns="http://www.w3.org/2000/svg"
		viewBox="0 0 24 24"
		fill="currentColor"
		class={className}
	>
		<path
			fill-rule="evenodd"
			d="M12 2.25a.75.75 0 0 1 .75.75v.756a49.106 49.106 0 0 1 9.152 1 .75.75 0 0 1-.152 1.485h-1.918l2.474 10.124a.75.75 0 0 1-.375.83 6.723 6.723 0 0 1-3.181.795 6.723 6.723 0 0 1-3.181-.795.75.75 0 0 1-.375-.83l2.474-10.124H12.75v13.28c1.293.076 2.534.343 3.697.776a.75.75 0 0 1-.262 1.455H7.815a.75.75 0 0 1-.262-1.455c1.163-.433 2.404-.7 3.697-.776V5.772H5.422l2.474 10.124a.75.75 0 0 1-.375.83 6.723 6.723 0 0 1-3.181.795 6.723 6.723 0 0 1-3.181-.795.75.75 0 0 1-.375-.83L3.258 5.772H1.339a.75.75 0 0 1-.152-1.486 49.105 49.105 0 0 1 9.152-.999V3a.75.75 0 0 1 .75-.75Zm-6.443 13.24a5.223 5.223 0 0 1-2.114 0l1.057-4.328 1.057 4.328Zm13.886 0a5.222 5.222 0 0 1-2.114 0l1.057-4.328 1.057 4.328Z"
			clip-rule="evenodd"
		/>
	</svg>
{:else if id === 'agents'}
	<svg
		aria-hidden="true"
		xmlns="http://www.w3.org/2000/svg"
		viewBox="0 0 24 24"
		fill="currentColor"
		class={className}
	>
		<path
			fill-rule="evenodd"
			d="M12 2.25a.75.75 0 0 1 .75.75v.518A6 6 0 0 1 18 9v3.75a3 3 0 0 1-3 3H9a3 3 0 0 1-3-3V9a6 6 0 0 1 5.25-5.482V3a.75.75 0 0 1 .75-.75ZM4.5 12.75a.75.75 0 0 1 .75-.75H6V9a6 6 0 0 1 .096-1.07A4.5 4.5 0 0 0 3 12c0 .414.336.75.75.75H4.5Zm15-.75a.75.75 0 0 1 .75.75h.75A.75.75 0 0 0 21 12a4.5 4.5 0 0 0-3.096-4.07c.063.349.096.706.096 1.07v3h.75a.75.75 0 0 1 .75.75ZM9.75 9.75a.75.75 0 0 0-1.5 0v1.5a.75.75 0 0 0 1.5 0v-1.5Zm6 0a.75.75 0 0 0-1.5 0v1.5a.75.75 0 0 0 1.5 0v-1.5ZM7.5 17.25a.75.75 0 0 1 .75.75c0 .414.336.75.75.75h6a.75.75 0 0 0 .75-.75.75.75 0 0 1 1.5 0 2.25 2.25 0 0 1-2.25 2.25H9a2.25 2.25 0 0 1-2.25-2.25.75.75 0 0 1 .75-.75Z"
			clip-rule="evenodd"
		/>
	</svg>
{:else if id === 'db'}
	<Database {className} {strokeWidth} />
{/if}
