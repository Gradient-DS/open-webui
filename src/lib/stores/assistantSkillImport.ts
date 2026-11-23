// [Gradient] Keep the selected bundle in memory while navigating to the create editor.
import { writable } from 'svelte/store';

export const assistantSkillImport = writable<File | null>(null);
