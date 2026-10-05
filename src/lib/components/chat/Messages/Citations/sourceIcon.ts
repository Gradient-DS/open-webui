import Document from '$lib/components/icons/Document.svelte';
import Outlook from '$lib/components/icons/Outlook.svelte';
import OneDrive from '$lib/components/icons/OneDrive.svelte';
import type { RawSourceObject } from './reduceSources';

const providerIcons = new Map([
	['outlook_mail', Outlook],
	['onedrive', OneDrive]
]);

export function sourceIcon(
	source: RawSourceObject
): { component: typeof Document; favicon?: never } | { component?: never; favicon: string } {
	const component = providerIcons.get(source.provider ?? '');
	if (component) return { component };

	// URLs only select the web favicon; they never identify a provider.
	const url = [source.url, source.name].find(
		(value) => value?.startsWith('https://') || value?.startsWith('http://')
	);
	return url
		? { favicon: `https://www.google.com/s2/favicons?sz=32&domain=${encodeURIComponent(url)}` }
		: { component: Document };
}
