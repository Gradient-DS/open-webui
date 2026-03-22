# Dev Notes System

Shared development notes track decisions, learnings, and context across developers and Claude Code sessions. The index serves as semantic long-term memory — not just a search index, but a compact representation of what has been decided, learned, and built over time.

## Structure

Top-level packages (first directory level under the repository root) may have a `dev_notes/` directory. Do not create `dev_notes/` in sub-packages or nested directories (e.g., use `document_processing/dev_notes/`, not `document_processing/distributed/dev_notes/`).

```
<top-level-package>/dev_notes/
  index.md    # Compact semantic index — always load when working on this package
  notes.md    # Append-only detailed notes
```

## When to Load the Index

**Before starting work on a package, check if it has a `dev_notes/` directory. If it does, read its `index.md` into context.** This applies when:
- The user asks to work on, modify, or debug code in that package
- The user asks questions about design decisions, architecture, or history of that package
- You need context on prior work to avoid duplicating effort or contradicting decisions

Do NOT load the index for unrelated work (e.g., working on `api/` does not require loading `document_processing/dev_notes/index.md`).

## Using Notes

1. **Read the index** to understand what's been documented, recall prior decisions and learnings, and find relevant entries
2. **Search `notes.md`** using Grep with section titles, dates, or keywords from the index to find specific entries
3. **Read specific sections** using Read with offset+limit — never load the entire notes file if it's large

## When to Write Notes

The developer decides when to write notes. Follow their lead.

However, if work is about to be committed and no note was written during the session, **proactively notify the developer** if the work involved any of the following:
- A non-trivial design decision (and why it was made)
- An experiment or investigation that produced learnings
- A significant implementation or architectural change
- A bug with a non-obvious root cause
- An approach that was tried and didn't work

Propose the note content for the developer to review. Do not write notes without the developer's approval.

Do NOT propose notes for:
- Routine small fixes, typos, or formatting changes
- Work that is fully self-explanatory from the code and commit message

## Writing a Note

Append to the **bottom** of `notes.md` (never insert in the middle):

```markdown
---

### [DD-MM-YYYY] Title of the Note

**Dev:** @github-handle: First Name

**Context:** Why we looked into this — the problem or question that prompted the work.

**What We Did:**
- Concise bullet points of actions taken

**Key Learnings:**
- What we discovered, decided, or concluded
- Include what didn't work and why, if applicable

**Related:** Links to other notes, experiments, PRs, or issues
```

Rules:
- Title should be descriptive and searchable (include component/feature names)
- Keep each section concise — notes are reference material, not journals
- Use the `---` horizontal rule as section delimiter between notes
- Always include the date, dev, and context fields

## Updating the Index

After adding a note, append a row to the table in `index.md`:

```markdown
| DD-MM-YYYY | Title | @handle | Summary | Keywords |
```

- **Summary**: One or two sentences capturing the key decision, outcome, or learning. This is the primary semantic memory — write it so that reading the index alone gives meaningful awareness of what happened.
- **Keywords**: 3-6 searchable terms (component names, patterns, technologies) for finding the full note in `notes.md`.
- The index must stay compact — one row per note.

## Amending Prior Notes

When a prior decision, learning, or result is changed by later work:

1. **Add an amendment annotation** directly below the title of the old note (this is the one exception to "never modify existing notes"):
   ```markdown
   ### [DD-MM-YYYY] Original Note Title

   > **Amended [DD-MM-YYYY]:** Reason for change — see [DD-MM-YYYY] Title of Newer Note.
   ```
   The original note content stays unchanged below the annotation.

2. **Create a new note** documenting the change, referencing the old note in `**Related:**`.

3. **Update the old index entry** by prepending the summary with an amendment marker:
   - When compactly describable: `[Amended, see DD-MM-YYYY, Topic] Original summary.`
   - When complex: `[Amended] Original summary.` (the full context lives in the note's annotation)

This preserves history while making it clear what's current. When reading the index, amended entries are immediately visible, and the forward reference lets you follow the chain to the current state.

<!-- TODO: Consider a specialized sub-agent for searching notes based on the index.
     When the index grows large, a dedicated agent could handle semantic search across
     notes, cross-referencing between packages, and surfacing relevant prior work. -->