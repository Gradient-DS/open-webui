/** [Gradient] Elapsed seconds as a stopwatch: 0:07, 4:12, 1:02:05; never negative. */
export const clock = (seconds: number): string => {
	const total = Math.max(0, Math.floor(seconds));
	const [hours, minutes, rest] = [
		Math.floor(total / 3600),
		Math.floor(total / 60) % 60,
		total % 60
	];
	const pad = (value: number) => String(value).padStart(2, '0');
	return hours ? `${hours}:${pad(minutes)}:${pad(rest)}` : `${minutes}:${pad(rest)}`;
};
