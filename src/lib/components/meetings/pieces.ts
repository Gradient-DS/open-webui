// [Gradient] Vergadering: a recording larger than one upload goes up in pieces, free of DOM and
// network so it unit-tests. soev-api stores each piece and the meeting agent joins them again.
import type { AudioRef } from './meeting';

/** The most one piece carries: half of what one upload may hold (64 MiB), to stay well under it. */
export const PIECE_BYTES = 32 * 1024 * 1024;

/** Sends one piece; `continues` is the id of the recording's first piece, absent for that piece. */
export type UploadPiece = (piece: Blob, name: string, continues?: string) => Promise<AudioRef>;

/** The recording cut into consecutive pieces of at most `size` bytes; joined in order they are it. */
export const piecesOf = (recording: Blob, size: number = PIECE_BYTES): Blob[] => {
	const pieces: Blob[] = [];
	for (let start = 0; start < recording.size; start += size) {
		pieces.push(recording.slice(start, start + size, recording.type));
	}
	return pieces.length > 0 ? pieces : [recording];
};

/**
 * Uploads a recording piece by piece, in order: the first as a file of its own, every later one
 * as continuing the first. Returns their references in order, as a `finish` lists them.
 */
export const uploadInPieces = async (
	upload: UploadPiece,
	recording: Blob,
	name: string,
	size: number = PIECE_BYTES
): Promise<AudioRef[]> => {
	const [first, ...rest] = piecesOf(recording, size);
	const head = await upload(first, name);
	const refs = [head];
	for (const piece of rest) refs.push(await upload(piece, name, head.id));
	return refs;
};
