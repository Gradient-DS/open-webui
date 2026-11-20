import { describe, expect, it } from 'vitest';

import type { AudioRef } from './meeting';
import { PIECE_BYTES, piecesOf, uploadInPieces, type UploadPiece } from './pieces';

const recording = (bytes: number) =>
	new Blob([new Uint8Array(bytes).map((_, index) => index % 251)], {
		type: 'audio/webm;codecs=opus'
	});

const bytesOf = async (blobs: Blob[]) =>
	new Uint8Array(await new Blob(blobs).arrayBuffer()) as Uint8Array;

describe('a recording in pieces', () => {
	it('keeps one upload well under the 64 MiB an upload may hold', () => {
		expect(PIECE_BYTES).toBeLessThanOrEqual(32 * 1024 * 1024);
	});

	it('cuts a recording into consecutive pieces that join back into it', async () => {
		const whole = recording(10);
		const pieces = piecesOf(whole, 4);
		expect(pieces.map((piece) => piece.size)).toEqual([4, 4, 2]);
		expect(pieces.every((piece) => piece.type === whole.type)).toBe(true);
		expect(await bytesOf(pieces)).toEqual(await bytesOf([whole]));
	});

	it('leaves a recording that fits one piece whole', () => {
		expect(piecesOf(recording(4), 4).map((piece) => piece.size)).toEqual([4]);
		expect(piecesOf(recording(0), 4)).toHaveLength(1);
	});

	it('uploads the first piece as a file and each later one as continuing it', async () => {
		const sent: { size: number; name: string; continues?: string }[] = [];
		const upload: UploadPiece = async (piece, name, continues) => {
			sent.push({ size: piece.size, name, continues });
			return { id: `id-${sent.length}`, size: piece.size } as AudioRef;
		};
		const refs = await uploadInPieces(upload, recording(10), 'meeting-1.webm', 4);
		expect(sent).toEqual([
			{ size: 4, name: 'meeting-1.webm', continues: undefined },
			{ size: 4, name: 'meeting-1.webm', continues: 'id-1' },
			{ size: 2, name: 'meeting-1.webm', continues: 'id-1' }
		]);
		expect(refs.map((ref) => ref.id)).toEqual(['id-1', 'id-2', 'id-3']);
	});

	it('uploads a small recording in one go, as before', async () => {
		const sent: (string | undefined)[] = [];
		const upload: UploadPiece = async (_piece, _name, continues) => {
			sent.push(continues);
			return { id: 'only' } as AudioRef;
		};
		expect(await uploadInPieces(upload, recording(3), 'meeting-1.webm', 4)).toEqual([
			{ id: 'only' }
		]);
		expect(sent).toEqual([undefined]);
	});
});
