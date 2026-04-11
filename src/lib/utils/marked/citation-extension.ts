export function citationExtension() {
	return {
		name: 'citation',
		level: 'inline' as const,

		start(src: string) {
			// Trigger on any [number], [number#suffix], 【number】, or 【number†metadata】 (Japanese fullwidth brackets)
			// We check for a digit immediately after [ or 【 to avoid matching arbitrary links
			return src.search(/(?:\[\d|【\d)/);
		},

		tokenizer(src: string) {
			// Avoid matching footnotes
			if (/^\[\^/.test(src)) return;

			// Match ONE OR MORE adjacent citation blocks:
			// - [1], [1,2], [1#foo], [4#bar] (standard brackets with optional #suffix)
			// - 【1】, 【1,2】, 【3†L1-L4】 (Japanese fullwidth brackets with optional †metadata)
			// Example matched: "[1][2,3][4#bar]" or "【1】【2,3】" or mixed
			const rule =
				/^(?:\[(?:\d+(?:#[^,\]\s]+)?(?:,\s*\d+(?:#[^,\]\s]+)?)*)\]|【(?:\d[\d,\s]*)(?:†[^】]*)?】)+/;
			const match = rule.exec(src);
			if (!match) return;

			const raw = match[0];

			// Extract ALL bracket groups inside the big match (both [] and 【】, including 【n†...】)
			const groupRegex = /(?:\[([^\]]+)\]|【([\d,\s]+)(?:†[^】]*)?】)/g;
			const ids: number[] = [];
			const citationIdentifiers: string[] = [];
			let m: RegExpExecArray | null;

			while ((m = groupRegex.exec(raw))) {
				if (m[1]) {
					// Standard [] bracket content, e.g. "1, 2#foo"
					const parts = m[1].split(',').map((p) => p.trim());

					parts.forEach((part) => {
						// Check if it starts with digit, optionally with #suffix
						const match = /^(\d+)(?:#(.+))?$/.exec(part);
						if (match) {
							const index = parseInt(match[1], 10);
							if (!isNaN(index)) {
								ids.push(index);
								// Store the full identifier ("1#foo" or "1")
								citationIdentifiers.push(part);
							}
						}
					});
				} else if (m[2]) {
					// Japanese 【】 bracket content, e.g. "1, 2"
					const parsed = m[2]
						.split(',')
						.map((n) => parseInt(n.trim(), 10))
						.filter((n) => !isNaN(n));

					parsed.forEach((index) => {
						ids.push(index);
						citationIdentifiers.push(String(index));
					});
				}
			}

			if (ids.length === 0) return;

			return {
				type: 'citation',
				raw,
				ids, // merged list of integers for legacy title lookup
				citationIdentifiers // merged list of full identifiers for granular targeting
			};
		},

		renderer(token: any) {
			// fallback text
			return token.raw;
		}
	};
}

export default function () {
	return {
		extensions: [citationExtension()]
	};
}
