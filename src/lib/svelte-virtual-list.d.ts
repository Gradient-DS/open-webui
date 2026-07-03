// Minimal ambient type declaration for @sveltejs/svelte-virtual-list.
// The package (v3) ships only a .svelte file with no TypeScript definitions.
// This declaration suppresses the implicit-any import error without
// constraining the component's props or slot API.
declare module '@sveltejs/svelte-virtual-list' {
	import type { SvelteComponentTyped } from 'svelte';
	export default class VirtualList extends SvelteComponentTyped<{
		items: unknown[];
		height?: string | number;
		itemHeight?: number;
		start?: number;
		end?: number;
	}> {}
}
