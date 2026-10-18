import type { ComponentType } from 'svelte';
import OneDrive from '$lib/components/icons/OneDrive.svelte';
import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
import Confluence from '$lib/components/icons/Confluence.svelte';

// [Gradient] Provider logos, kept apart from the registry so chat components can
// badge a cloud item without loading the vendor pickers.
const badges: Record<string, { icon: ComponentType; label: string }> = {
	onedrive: { icon: OneDrive, label: 'OneDrive' },
	google_drive: { icon: GoogleDrive, label: 'Google Drive' },
	confluence: { icon: Confluence, label: 'Confluence' }
};

export function providerBadge(
	kind: string | null | undefined
): { icon: ComponentType; label: string } | null {
	return kind && Object.hasOwn(badges, kind) ? badges[kind] : null;
}
