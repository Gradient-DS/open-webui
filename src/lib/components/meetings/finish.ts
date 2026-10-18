// [Gradient] Vergadering: upload a meeting's recording parts and send `finish` with them, in order.
import { sendMeetingInput, uploadMeetingAudio } from '$lib/apis/meetings';
import type { AudioRef } from './meeting';
import { partStore } from './parts';
import { sendWhenIdle } from './recorder';

export const audioExtension = (type: string) =>
	(type.split('/')[1] ?? 'webm').split(';')[0].trim() || 'webm';

/** Every part becomes one audio object; an empty list finishes from the live transcript alone. */
export const finishMeeting = async (token: string, meetingId: string, recordings: Blob[]) => {
	const audio_refs: AudioRef[] = [];
	for (const [index, blob] of recordings.entries()) {
		const name = `meeting-${index + 1}.${audioExtension(blob.type)}`;
		audio_refs.push((await uploadMeetingAudio(token, blob, name)) as AudioRef);
	}
	await sendWhenIdle(() => sendMeetingInput(token, meetingId, { type: 'finish', audio_refs }));
	await partStore().remove(meetingId);
};
