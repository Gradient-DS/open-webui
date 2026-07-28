import { describe, it, expect } from 'vitest';
import { isLocalKnowledgeType, canEditStructure } from './structure';

describe('isLocalKnowledgeType', () => {
	it('true for local and untyped', () => {
		expect(isLocalKnowledgeType('local')).toBe(true);
		expect(isLocalKnowledgeType(null)).toBe(true);
		expect(isLocalKnowledgeType(undefined)).toBe(true);
		expect(isLocalKnowledgeType('')).toBe(true);
	});

	it('false for cloud-sync and integration-provider types', () => {
		expect(isLocalKnowledgeType('onedrive')).toBe(false);
		expect(isLocalKnowledgeType('google_drive')).toBe(false);
		expect(isLocalKnowledgeType('confluence')).toBe(false);
		expect(isLocalKnowledgeType('some_push_provider')).toBe(false);
	});
});

describe('canEditStructure', () => {
	it('allows local + write access', () => {
		expect(canEditStructure({ type: 'local', write_access: true })).toBe(true);
		expect(canEditStructure({ write_access: true })).toBe(true);
	});

	it('denies local without write access', () => {
		expect(canEditStructure({ type: 'local', write_access: false })).toBe(false);
		expect(canEditStructure({ type: 'local' })).toBe(false);
	});

	it('denies cloud-sync KBs regardless of write access', () => {
		expect(canEditStructure({ type: 'onedrive', write_access: true })).toBe(false);
		expect(canEditStructure({ type: 'google_drive', write_access: true })).toBe(false);
		expect(canEditStructure({ type: 'confluence', write_access: true })).toBe(false);
	});

	it('denies integration-provider (push) KBs', () => {
		expect(canEditStructure({ type: 'servicenow', write_access: true })).toBe(false);
	});

	it('denies null/undefined knowledge', () => {
		expect(canEditStructure(null)).toBe(false);
		expect(canEditStructure(undefined)).toBe(false);
	});
});
