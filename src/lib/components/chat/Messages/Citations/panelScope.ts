import type { DisplayCitation } from './reduceSources';

/**
 * [Gradient] Per-message source-panel scope.
 *
 * The agent tags each cumulative source with provenance flags: `current_turn`
 * (a tool retrieved it this turn) and `cited_this_turn` (the model wrote its
 * `[N]` in this turn's answer, including a cross-turn re-cite of an earlier
 * turn's source). The bottom panel shows the union — everything this message
 * actually surfaced — while inline `[N]` still resolves against the full
 * cumulative `sources` array.
 *
 * Gating is on `cited_this_turn` PRESENCE, deliberately NOT on `current_turn`.
 * A pre-change agent emits only `current_turn` (the `cited_this_turn` field
 * was added alongside this panel logic). Gating on `current_turn` would make
 * such an agent filter the panel and HIDE cross-turn re-cites (current_turn
 * false, cited_this_turn absent) behind an empty panel — a regression. When no
 * citation carries `cited_this_turn` (legacy chats, a pre-change agent, or an
 * upstream provider) we show everything, preserving the prior behavior.
 */
export function scopePanelCitations(citations: DisplayCitation[]): DisplayCitation[] {
	const speaksProvenance = citations.some((c) => c.cited_this_turn !== undefined);
	if (!speaksProvenance) return citations;
	return citations.filter((c) => c.current_turn || c.cited_this_turn);
}
