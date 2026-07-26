#!/usr/bin/env node
/**
 * CI audit: fail if any third-party package pinned in package-lock.json was
 * published to the npm registry less than MIN_AGE_DAYS ago.
 *
 * Rationale: a minimum publish age gives the ecosystem a window to catch and
 * pull freshly-published supply-chain compromises before they can land here.
 * Mirrors the Python-side `uv` exclude-newer policy (see pyproject.toml).
 *
 * Usage: node scripts/check-lockfile-age.mjs [--lockfile <path>]
 *
 * Failure semantics (deliberate): this script exits non-zero both on actual
 * age violations AND on any registry lookup that could not be verified after
 * a retry. A network-dead or rate-limited CI run must fail loud, never go
 * green by silently skipping packages it couldn't check.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const MIN_AGE_DAYS = 14;
const CONCURRENCY = 10;
const REGISTRY = 'https://registry.npmjs.org';
// Our own scope — always exempt, never subject to the cooldown.
const ALLOWLIST_PREFIXES = ['@gradient-ds/'];

function parseArgs(argv) {
	let lockfile = './package-lock.json';
	for (let i = 0; i < argv.length; i++) {
		if (argv[i] === '--lockfile' && argv[i + 1] !== undefined) {
			lockfile = argv[i + 1];
			i++;
		}
	}
	return { lockfile };
}

/**
 * Derive the real npm package name for a `packages` entry key.
 *
 * Known limitation (not hit by this repo today — 0 `link: true` entries, 0
 * non-registry.npmjs.org `resolved` values in the committed lockfile, so
 * nothing currently falls into either gap below): npm workspace setups can
 * produce `packages` keys with no `node_modules/` segment at all (a bare
 * local path like `"packages/foo"`), for which `key.lastIndexOf('node_modules/')`
 * returns -1 and this function returns null — that entry is then silently
 * dropped by `collectEntries` below (uncounted, unlogged), same as the
 * separate relative-`resolved`-with-no-scheme case noted there. If this repo
 * ever adopts npm workspaces, both gaps need an explicit, logged skip path
 * (or a workspace-aware name/version source) rather than a silent drop.
 */
function packageNameFor(key, entry) {
	// npm alias entries (`npm:` specifiers, e.g. the *-cjs shims) carry an
	// explicit `name` that differs from the node_modules path — trust it.
	if (entry.name) return entry.name;
	const idx = key.lastIndexOf('node_modules/');
	if (idx === -1) return null;
	return key.slice(idx + 'node_modules/'.length);
}

function isAllowlisted(name) {
	return ALLOWLIST_PREFIXES.some((prefix) => name.startsWith(prefix));
}

/** Read the lockfile and return the { name, version } pairs worth auditing. */
function collectEntries(lockfilePath) {
	const data = JSON.parse(readFileSync(lockfilePath, 'utf8'));
	const packages = data.packages ?? {};
	const entries = [];
	for (const [key, entry] of Object.entries(packages)) {
		if (key === '') continue; // the project's own root entry
		if (entry.link) continue; // workspace symlink, not a real install
		if (!entry.version) continue; // nothing to look up an age for
		const resolved = entry.resolved ?? '';
		// Not npm-registry-resolvable (git checkout, local path, tarball URL) —
		// out of scope for a registry publish-date audit. Known gap (0 hits in
		// this repo's lockfile today): an npm-workspace local dependency whose
		// resolved is a bare relative path with no scheme would NOT match this
		// regex and would fall through to a (likely incorrect) registry lookup
		// by name instead of being skipped here.
		if (/^(git\+|git:|file:|https?:\/\/(?!registry\.npmjs\.org))/.test(resolved)) continue;
		const name = packageNameFor(key, entry);
		// name is null for workspace-local packages keys with no node_modules/
		// segment (see packageNameFor's doc comment) — silently dropped, same
		// as the gap above. Neither case exists in this lockfile today (no npm
		// workspaces here), but both are worth revisiting if that changes.
		if (!name || isAllowlisted(name)) continue;
		entries.push({ name, version: entry.version });
	}
	return entries;
}

async function fetchJson(url, attempts = 2) {
	let lastErr;
	for (let i = 0; i < attempts; i++) {
		try {
			const res = await fetch(url, {
				headers: { 'user-agent': 'open-webui-lockfile-age-check' }
			});
			if (res.status === 404) return { status: 404 };
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			return { status: 200, body: await res.json() };
		} catch (err) {
			lastErr = err;
		}
	}
	throw lastErr;
}

/** Run `worker` over `items` with at most `concurrency` in flight. */
async function runPool(items, concurrency, worker) {
	let next = 0;
	async function lane() {
		while (next < items.length) {
			const item = items[next++];
			await worker(item);
		}
	}
	await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, lane));
}

async function main() {
	const { lockfile } = parseArgs(process.argv.slice(2));
	const lockfilePath = resolve(process.cwd(), lockfile);
	const entries = collectEntries(lockfilePath);

	const byName = new Map();
	for (const entry of entries) {
		if (!byName.has(entry.name)) byName.set(entry.name, []);
		byName.get(entry.name).push(entry.version);
	}
	const names = [...byName.keys()];

	console.log(
		`Checking ${entries.length} package entries (${names.length} unique names) against a ${MIN_AGE_DAYS}-day minimum publish age...`
	);

	const cutoffMs = Date.now() - MIN_AGE_DAYS * 24 * 60 * 60 * 1000;
	const violations = [];
	const unverifiable = [];

	await runPool(names, CONCURRENCY, async (name) => {
		const url = `${REGISTRY}/${name}`;
		let result;
		try {
			result = await fetchJson(url);
		} catch (err) {
			unverifiable.push({ name, reason: err.message ?? String(err) });
			return;
		}
		if (result.status === 404) {
			// A package we have locked but the registry has never heard of is
			// itself an anomaly worth failing on, not a reason to skip it.
			unverifiable.push({ name, reason: '404 from registry' });
			return;
		}
		const time = result.body.time ?? {};
		for (const version of byName.get(name)) {
			const published = time[version];
			if (!published) {
				unverifiable.push({ name, version, reason: 'no publish time in registry metadata' });
				continue;
			}
			const publishedMs = Date.parse(published);
			if (publishedMs > cutoffMs) {
				const ageDays = (Date.now() - publishedMs) / (24 * 60 * 60 * 1000);
				violations.push({ name, version, published, ageDays });
			}
		}
	});

	// Report every failure category found in this run — do not stop at the
	// first one — so a single re-run surfaces everything to fix at once.
	if (unverifiable.length > 0) {
		console.error(`\n${unverifiable.length} package(s) could not be verified after retry:`);
		for (const u of unverifiable) {
			console.error(`  - ${u.name}${u.version ? `@${u.version}` : ''}: ${u.reason}`);
		}
	}

	if (violations.length > 0) {
		violations.sort((a, b) => a.ageDays - b.ageDays);
		console.error(
			`\n${violations.length} package(s) violate the ${MIN_AGE_DAYS}-day minimum age policy:\n`
		);
		for (const v of violations) {
			console.error(
				`  - ${v.name}@${v.version}: published ${v.published} (${v.ageDays.toFixed(1)} days ago)`
			);
		}
		console.error(
			`\nRoll these back to a version published before ${new Date(cutoffMs).toISOString()}.`
		);
	}

	if (unverifiable.length > 0 || violations.length > 0) {
		console.error(
			'\nFAIL: treating unverifiable packages as a failure (not a pass), and/or found age violations.'
		);
		process.exitCode = 1;
		return;
	}

	console.log(`\nOK: all ${entries.length} package entries are at least ${MIN_AGE_DAYS} days old.`);
}

main().catch((err) => {
	console.error('Unexpected error running lockfile age check:', err);
	process.exitCode = 1;
});
