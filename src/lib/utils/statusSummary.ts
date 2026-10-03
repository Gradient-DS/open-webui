// [Gradient] The header of a finished block of agent activity: what it did, in a sentence ("Nagedacht en 3
// websites gelezen"), instead of its newest line.

type Translate = (key: string, options?: Record<string, unknown>) => string;

type Item = { kind?: string; action?: string };

// What each kind of step reads as, singular and plural, in the order a sentence lists them.
const PHRASES: Array<{ actions: string[]; one: string; many: string }> = [
	{ actions: ['web_search'], one: 'searched the web', many: 'searched the web {{count}} times' },
	{ actions: ['fetch'], one: 'read a website', many: 'read {{count}} websites' },
	{
		actions: ['search', 'find_documents'],
		one: 'searched the knowledge base',
		many: 'searched the knowledge base {{count}} times'
	},
	{
		actions: ['open_document', 'list_documents'],
		one: 'read a document',
		many: 'read {{count}} documents'
	},
	{ actions: ['create_office_file'], one: 'created a file', many: 'created {{count}} files' },
	{ actions: ['edit_office_file'], one: 'edited a file', many: '{{count}} files edited' }
];

const OTHER = { one: 'took a step', many: 'took {{count}} steps' };

/** The sentence for a block's items, or `null` when it holds only one step. */
export const describeBlock = (items: Item[], t: Translate): string | null => {
	const counts = new Map<string, number>();
	let thought = false;
	let other = 0;
	for (const item of items) {
		if (item?.kind === 'reasoning') {
			thought = true;
		} else if (item?.kind !== 'content' && item?.action && item.action !== 'summary') {
			const phrase = PHRASES.find((p) => p.actions.includes(item.action as string));
			if (phrase) counts.set(phrase.one, (counts.get(phrase.one) ?? 0) + 1);
			else other += 1;
		}
	}
	const parts = [
		...(thought ? [t('thought')] : []),
		...PHRASES.filter((p) => counts.has(p.one)).map((p) => {
			const count = counts.get(p.one) ?? 0;
			return count === 1 ? t(p.one) : t(p.many, { count });
		}),
		// An Office agent's own steps are the making of its file.
		...(other && !counts.has('created a file') && !counts.has('edited a file')
			? [other === 1 ? t(OTHER.one) : t(OTHER.many, { count: other })]
			: [])
	];
	if (parts.length === 0 || (parts.length === 1 && items.length <= 1)) return null;
	const sentence =
		parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(', ')} ${t('and')} ${parts.at(-1)}`;
	return sentence.charAt(0).toUpperCase() + sentence.slice(1);
};
