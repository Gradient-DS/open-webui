import { describe, expect, it } from 'vitest';
import { composerFeatures, composerFromFeatures, composerPreferences } from './composerPreferences';
import { inputItemOrder } from '$lib/components/chat/MessageInput/inputItems';

describe('retired composer features', () => {
	it('never restores or serializes image generation from saved chats or model defaults', () => {
		const saved = composerFromFeatures({ image_generation: true });
		const defaults = composerPreferences({}, { imageGenerationEnabled: true });
		expect(saved.imageGenerationEnabled).toBe(false);
		expect(defaults.imageGenerationEnabled).toBe(false);
		expect(composerFeatures(saved)).not.toHaveProperty('image_generation');
		expect(inputItemOrder()).not.toContain('image_generation');
	});
});
