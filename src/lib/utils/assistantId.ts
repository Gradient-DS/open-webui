/** Ids for new assistants: the slug of the name, made unique where it clashes. */

export const slugify = (name: string): string =>
	name
		.toLowerCase()
		.trim()
		.replace(/[^a-z0-9]+/g, '-')
		.replace(/^-+|-+$/g, '');

const suffix = (): string => crypto.randomUUID().slice(0, 6);

/** The id with a short random suffix, for when the plain one is taken. */
export const suffixedId = (id: string): string => `${id}-${suffix()}`;

/**
 * The slug of the name, suffixed only when it clashes with an id in `takenIds`.
 * `takenIds` holds the models the user can see; the server knows the rest (see `isIdTaken`).
 */
export const uniqueSlug = (name: string, takenIds: Iterable<string>): string => {
	const slug = slugify(name);
	if (!slug) return slug;
	return new Set(takenIds).has(slug) ? suffixedId(slug) : slug;
};

/** Whether a create was refused because the id belongs to a model, visible to the user or not. */
export const isIdTaken = (error: unknown): boolean =>
	typeof error === 'string' && error.includes('model id is already registered');

/**
 * Create with `info.id`, and once more with a suffixed id when the server says it is taken:
 * another user's assistant with the same name is not in the user's model list.
 * On the retry `info.id` is changed in place, so the caller continues with the id that was saved.
 */
export const createWithFreeId = async <T extends { id: string }, R>(
	info: T,
	create: (info: T) => Promise<R>
): Promise<R> => {
	try {
		return await create(info);
	} catch (error) {
		if (!isIdTaken(error)) throw error;
		info.id = suffixedId(info.id);
		return await create(info);
	}
};
