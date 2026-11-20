// [Gradient] Vergadering: upload a meeting's recordings and send `finish` with them, in order.
import { sendMeetingInput, uploadMeetingAudio } from '$lib/apis/meetings';
import type { AudioRef } from './meeting';
import { partStore } from './parts';
import { uploadInPieces, type UploadPiece } from './pieces';
import { sendWhenIdle } from './recorder';

export const audioExtension = (type: string) =>
	(type.split('/')[1] ?? 'webm').split(';')[0].trim() || 'webm';

/**
 * Every recording goes up as one audio object, or piece by piece when it is larger than one
 * upload; an empty list finishes from the live transcript alone.
 */
export const finishMeeting = async (token: string, meetingId: string, recordings: Blob[]) => {
	const upload: UploadPiece = async (piece, name, continues) =>
		(await uploadMeetingAudio(token, piece, name, continues)) as AudioRef;
	const audio_refs: AudioRef[] = [];
	for (const [index, blob] of recordings.entries()) {
		const name = `meeting-${index + 1}.${audioExtension(blob.type)}`;
		audio_refs.push(...(await uploadInPieces(upload, blob, name)));
	}
	await sendWhenIdle(() => sendMeetingInput(token, meetingId, { type: 'finish', audio_refs }));
	await partStore().remove(meetingId);
};
