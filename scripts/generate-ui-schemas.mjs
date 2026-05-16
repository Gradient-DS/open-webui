#!/usr/bin/env node
/**
 * Generate TypeScript types from committed JSON Schemas.
 *
 * Source: ../genai-utils/agents/agents/capabilities/components/schemas/*.schema.json
 * Output: src/lib/types/present_ui/<name>.ts + index.ts barrel
 *
 * Run via `npm run generate:ui-schemas`. CI runs this and diffs the
 * result against the committed output to catch drift between the
 * Pydantic models and the frontend TS types.
 *
 * Scope note: this converter handles the subset of JSON Schema that
 * our component schemas use today — primitives, arrays, nullable
 * unions (`{anyOf: [{type: 'X'}, {type: 'null'}]}`), and required
 * field lists. It is intentionally minimal; extending it for nested
 * objects or refs should happen when a schema first needs that
 * feature, not preemptively.
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const SCHEMAS_DIR = resolve(
	__dirname,
	'../../genai-utils/agents/agents/capabilities/components/schemas'
);
const OUTPUT_DIR = resolve(__dirname, '../src/lib/types/present_ui');

/**
 * Convert a JSON-Schema type spec into a TypeScript type expression.
 * Handles primitives, arrays, `anyOf` unions (including null), and
 * `$ref` lookups into a `$defs` table (Pydantic-style nested models).
 */
function tsTypeFor(spec, defs) {
	if (spec.$ref) {
		const refName = spec.$ref.replace(/^#\/\$defs\//, '');
		return refName;
	}
	if (spec.anyOf) {
		return spec.anyOf.map((branch) => tsTypeFor(branch, defs)).join(' | ');
	}
	if (spec.type === 'string') return 'string';
	if (spec.type === 'number') return 'number';
	if (spec.type === 'integer') return 'number';
	if (spec.type === 'boolean') return 'boolean';
	if (spec.type === 'null') return 'null';
	if (spec.type === 'array') {
		return `Array<${tsTypeFor(spec.items ?? {}, defs)}>`;
	}
	if (spec.type === 'object') {
		return 'Record<string, unknown>';
	}
	return 'unknown';
}

/**
 * Render a single `export interface NAME { … }` block from a Pydantic
 * object schema (the top-level component schema or an entry in $defs).
 */
function renderInterface(typeName, schema, defs) {
	const required = new Set(schema.required ?? []);
	const lines = [];
	lines.push(`interface ${typeName} {`);
	for (const [field, fieldSpec] of Object.entries(schema.properties ?? {})) {
		if (fieldSpec.description) {
			lines.push(`\t/** ${fieldSpec.description} */`);
		}
		const optional = required.has(field) ? '' : '?';
		lines.push(`\t${field}${optional}: ${tsTypeFor(fieldSpec, defs)};`);
	}
	lines.push('}');
	return lines.join('\n');
}

/**
 * Render one .ts module from a JSON Schema. Emits `$defs` interfaces
 * first (so referenced names exist before the props interface uses
 * them), then the top-level interface.
 */
function renderModule(name, schema) {
	const typeName = schema.title ?? toPascal(name);
	const defs = schema.$defs ?? {};
	const lines = [];
	lines.push('// AUTO-GENERATED FILE — do not edit by hand.');
	lines.push('// Regenerate with: npm run generate:ui-schemas');
	lines.push(`// Source schema: ${name}.schema.json`);
	lines.push('');
	for (const defName of Object.keys(defs).sort()) {
		lines.push(`export ${renderInterface(defName, defs[defName], defs)}`);
		lines.push('');
	}
	lines.push(`export ${renderInterface(typeName, schema, defs)}`);
	lines.push('');
	return lines.join('\n');
}

function toPascal(name) {
	return name
		.split(/[_-]/g)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join('');
}

function renderBarrel(modules) {
	const lines = ['// AUTO-GENERATED FILE — do not edit by hand.', ''];
	for (const { name, defs } of modules) {
		const props = `${toPascal(name)}Props`;
		const exported = [props, ...Object.keys(defs).sort()];
		lines.push(`export type { ${exported.join(', ')} } from './${name}';`);
	}
	lines.push('');
	return lines.join('\n');
}

if (!existsSync(SCHEMAS_DIR)) {
	console.error(`schemas dir not found: ${SCHEMAS_DIR}`);
	process.exit(2);
}
mkdirSync(OUTPUT_DIR, { recursive: true });

const files = readdirSync(SCHEMAS_DIR)
	.filter((f) => f.endsWith('.schema.json'))
	.sort();
const modules = [];
for (const file of files) {
	const name = file.replace(/\.schema\.json$/, '');
	const schema = JSON.parse(readFileSync(resolve(SCHEMAS_DIR, file), 'utf-8'));
	// Force interface name to <Name>Props so callers see ChoiceProps,
	// DocumentListProps. Override the schema's "title" — they happen
	// to align with our naming convention, but explicit is safer.
	schema.title = `${toPascal(name)}Props`;
	writeFileSync(resolve(OUTPUT_DIR, `${name}.ts`), renderModule(name, schema));
	modules.push({ name, defs: schema.$defs ?? {} });
}
writeFileSync(resolve(OUTPUT_DIR, 'index.ts'), renderBarrel(modules));

console.log(`Generated ${modules.length} type module(s) in ${OUTPUT_DIR}`);
