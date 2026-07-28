/**
 * KB-type gating for the converged (upstream v0.10.2) directory UI.
 *
 * Structure writes — create/rename/delete/move directories, drag-drop file
 * moves, uploads into a directory, incremental directory sync, Reset — are
 * only allowed on local/untyped KBs the user can write to. Cloud-synced KBs
 * (onedrive / google_drive / confluence) get read-only structure browsing
 * because the sync pipeline owns their hierarchy; integration-provider
 * (push) KBs are browse-only because files arrive via the API.
 */

export interface StructureGateKnowledge {
	type?: string | null;
	write_access?: boolean;
}

/** Local/untyped KBs are the only ones whose structure users own. */
export function isLocalKnowledgeType(type: string | null | undefined): boolean {
	return !type || type === 'local';
}

/**
 * Single derived guard for every structure-write affordance (plan decision 5):
 * New directory, dir rename/delete/move, file move drag-drop,
 * uploads-into-directory, incremental sync, Reset.
 */
export function canEditStructure(knowledge: StructureGateKnowledge | null | undefined): boolean {
	if (!knowledge) return false;
	return !!knowledge.write_access && isLocalKnowledgeType(knowledge.type);
}
