// [Gradient] Vergadering: capture the meeting audio from the microphone, a shared tab, or both mixed.

export type AudioSource = 'mixed' | 'microphone' | 'display';

/** The shared surface carried no audio (sharing audio was off, or a window Chrome cannot capture). */
export class NoAudioTrackError extends Error {
	constructor() {
		super('no_audio_track');
		this.name = 'NoAudioTrackError';
	}
}

export type Capture = {
	/** The stream to record and visualise. */
	stream: MediaStream;
	/** The source tracks; when one of them ends, the recording should end. */
	tracks: MediaStreamTrack[];
	/** Stops every track and closes the mixer. Safe to call twice. */
	release: () => void;
};

/**
 * Chrome hints: preselect the tab list, never offer this soev tab, and ask for tab audio.
 * Browsers ignore members they do not know.
 */
export const displayMediaOptions = () => ({
	video: { displaySurface: 'browser' },
	audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
	preferCurrentTab: false,
	selfBrowserSurface: 'exclude',
	systemAudio: 'include',
	surfaceSwitching: 'include'
});

const MIC_CONSTRAINTS = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };

const stopAll = (tracks: MediaStreamTrack[]) => {
	for (const track of tracks) track.stop();
};

const shareDisplay = async (): Promise<MediaStream> => {
	const shared = await navigator.mediaDevices.getDisplayMedia(
		displayMediaOptions() as DisplayMediaStreamOptions
	);
	stopAll(shared.getVideoTracks());
	const audio = shared.getAudioTracks();
	if (audio.length === 0) {
		stopAll(shared.getTracks());
		throw new NoAudioTrackError();
	}
	return new MediaStream(audio);
};

const once = (fn: () => void) => {
	let done = false;
	return () => {
		if (!done) {
			done = true;
			fn();
		}
	};
};

/** Must be called from the click that starts the meeting: the share picker needs that gesture. */
export const captureAudio = async (source: AudioSource): Promise<Capture> => {
	if (source === 'microphone') {
		const stream = await navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS });
		const tracks = stream.getTracks();
		return { stream, tracks, release: once(() => stopAll(tracks)) };
	}
	if (source === 'display') {
		const stream = await shareDisplay();
		const tracks = stream.getTracks();
		return { stream, tracks, release: once(() => stopAll(tracks)) };
	}

	// Created inside the gesture so it is not born suspended.
	const context = new AudioContext();
	let tab: MediaStream | null = null;
	let mic: MediaStream | null = null;
	try {
		tab = await shareDisplay();
		mic = await navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS });
		const mix = context.createMediaStreamDestination();
		context.createMediaStreamSource(tab).connect(mix);
		context.createMediaStreamSource(mic).connect(mix);
		await context.resume().catch(() => {});
		const tracks = [...tab.getTracks(), ...mic.getTracks()];
		return {
			stream: mix.stream,
			tracks,
			release: once(() => {
				stopAll(tracks);
				context.close().catch(() => {});
			})
		};
	} catch (error) {
		stopAll([...(tab?.getTracks() ?? []), ...(mic?.getTracks() ?? [])]);
		context.close().catch(() => {});
		throw error;
	}
};
