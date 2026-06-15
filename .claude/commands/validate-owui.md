---
description: Validate a running Open WebUI stack end-to-end by driving Chromium (Playwright MCP) via the owui-e2e Sonnet subagent. Logs in as the seeded e2e admin and clicks through a smoke flow (chat → send → assert response → Knowledge), or a custom scenario you describe.
argument-hint: "[url=http://localhost:PORT | branch=feat/... | stack=stack-N] [email=... password=...] [free-text scenario]"
---

Run an end-to-end validation of a running Open WebUI deployment by dispatching the
**`owui-e2e`** subagent (it runs on Sonnet 4.6 and drives a headed Chromium browser
through the `playwright` MCP server). You — the orchestrating session — only resolve
the target URL and dispatch; the subagent does all the clicking and returns a report.

Arguments (all optional): `$ARGUMENTS`

## Step 1 — Preflight

Confirm the `playwright` MCP server is connected (its `browser_*` tools are
available). It's defined in `open-webui/.mcp.json`. If the tools aren't present, tell
the user to approve/restart so the MCP server connects, then stop.

## Step 2 — Resolve the target URL

Parse `$ARGUMENTS`:

- If it contains `url=<value>`, use that value verbatim as the target URL.
- Else if it contains `branch=<value>` or `stack=<value>`, resolve the `owui_fe`
  port from the stack registry.
- Else, auto-detect: if exactly one stack is running, use it; otherwise list the
  running stacks and ask the user which one.

Run this helper to resolve from the registry (`~/.config/soev/stacks.json`), passing
any `branch=`/`stack=` value as the first arg (or nothing for auto-detect):

```
python3 -c "import json,os,sys; sel=sys.argv[1] if len(sys.argv)>1 else ''; p=os.path.expanduser('~/.config/soev/stacks.json'); d=json.load(open(p)); st=d.get('stacks',[]); m=[s for s in st if sel and (s.get('branch')==sel or s.get('project_name')==sel or ('stack-'+str(s.get('index')))==sel)] if sel else st; print('CANDIDATES:'+json.dumps([{'branch':s.get('branch'),'stack':'stack-'+str(s.get('index')),'owui_fe':s.get('ports',{}).get('owui_fe'),'status':s.get('status')} for s in m]))"
```

If exactly one candidate, the target URL is `http://localhost:<owui_fe>`. If zero or
many, show the candidates and ask the user to disambiguate (or to pass `url=`).

If `$ARGUMENTS` contains `email=<value>` and/or `password=<value>`, capture them as a
**credential override** to pass to the subagent (useful for stacks that predate the
seeded `e2e@soev.local` account, or to test as a specific user). Otherwise the
subagent falls back to the seeded admin creds in `.env`.

Everything in `$ARGUMENTS` that is **not** a `url=`/`branch=`/`stack=`/`email=`/
`password=` token is the optional free-text **scenario**.

## Step 3 — Dispatch the subagent

Call the Agent tool with `subagent_type: owui-e2e` and a prompt like:

> Target URL: `<resolved url>`
>
> Run the default OWUI smoke flow (open → log in as the seeded admin if needed →
> assert chat + model selector → new chat → send "Reply with exactly the single
> word: pong" → assert a non-empty response → open Workspace → Knowledge → assert it
> renders → console check). Capture screenshots and return the structured report.
>
> Credentials: `<email/password override if provided, else "use the seeded admin in .env">`
>
> Scenario (if any): `<scenario text, or "none — default smoke flow only">`

## Step 4 — Relay the result

Surface the subagent's PASS/FAIL report to the user verbatim, plus a one-line summary
(target URL, result, model). If it FAILED, point at the most relevant screenshot path
and the failing step. Do not re-run automatically — let the user decide.
