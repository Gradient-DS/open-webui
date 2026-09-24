import type { TreeStatusCounts } from '../utils/treeStatus';

export interface DirectoryItem {
	id: string;
	name: string;
	created_at: number;
	updated_at: number;
	child_count?: number;
	status_counts?: TreeStatusCounts;
	schedule_id?: string | null;
	placeholder?: true;
	parent_id?: string | null;
}
