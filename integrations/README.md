# Integrations: the IKG MCP server on AI platforms

One backend, the IKG MCP server (`ikg mcp`, docs/03 §13), packaged for each platform (docs/08 B2–B4).

| Platform | Package | Status |
|---|---|---|
| **Claude Code** | Plugin [`claude/ikg`](claude/ikg): the MCP server over HTTP and the `ikg-scholar` skill | Done; tested below |
| **Claude Desktop** | The MCP server over stdio (README) and the `ikg-scholar` skill, uploaded | Works locally today |
| **claude.ai** | A custom connector (remote MCP) and the skill | Read-only tools without a token through `IKG_MCP_PUBLIC` (below); with `ikg_ask`, needs OAuth (docs/08 §E) |
| **ChatGPT** | A custom GPT with Actions from the app's OpenAPI document ([`chatgpt/`](chatgpt)), or the MCP server as a plugin in public mode | GPT ready to build (below); the plugin connected in public mode, 2026-11-10 |
| Microsoft 365 Copilot | A declarative agent with an API plugin | B4 |

## Claude Code

**From the plugin marketplace** in this repository (needs access to the repository):

```
/plugin marketplace add vkonda2000/indic-knowledge-graph
/plugin install indic-knowledge-graph@indic-knowledge-graph
```

Claude Code then asks for the server URL (default `http://127.0.0.1:8001/mcp`, the Docker `mcp`
service) and its token (`IKG_MCP_TOKEN` from the server's `.env`). The plugin marks the token
`sensitive`, which per the Claude Code plugin reference stores it in secure storage, not in
`settings.json`. Any URL other than a loopback address must be `https://`, or the token travels in
clear. Start the server first: `docker compose --profile mcp up -d mcp`.

**From a checkout**, without installing: `claude --plugin-dir integrations/claude/ikg`.

**Developers** working in this repository can skip HTTP and run the server over stdio:
`claude mcp add ikg -- ikg mcp` (with the `.venv` active).

The plugin contains:

- `.mcp.json`: the `ikg` server, as Streamable HTTP with `Authorization: Bearer <token>`.
- `skills/ikg-scholar/`: when to use which tool, how to cite (`ref` and `passage_id`, translator),
  keeping text and commentary apart, renderings vs. concepts, stories told in several works, and
  abstaining; with SPARQL examples and worked examples in `references/`.

## Claude Desktop and claude.ai

These are as we understand the products at the time of writing (November 2026); check the current
Claude help pages before relying on them.

- **The skill:** zip the folder `claude/ikg/skills/ikg-scholar` and upload it in claude.ai's
  settings, where custom skills are added (under Capabilities at the time of writing).
- **The server, Desktop:** stdio, as in the README's MCP section.
- **The server, claude.ai:** a custom connector takes a public HTTPS URL and authenticates with
  OAuth or not at all; there is no field for a static token. Without OAuth, only public mode works
  (next section): the read-only tools, no `ikg_ask`. The full server waits for OAuth (docs/08 §E).

## Public mode: the MCP server without a token

Clients that can't send a static token (ChatGPT's *Plugins → Add custom MCP server* with *No
authentication*, claude.ai's custom connectors) can use the read-only tools when the server runs with
`IKG_MCP_PUBLIC=on` in `.env` (`docker compose --profile mcp up -d mcp`). Then:

- no token is needed, and `IKG_MCP_TOKEN` is ignored (the server says so at start);
- `ikg_ask`, the one tool that can cost money, is not served at all;
- every tool call counts against `IKG_MCP_PUBLIC_LIMIT` (default 600 an hour, shared by everyone),
  and at most two SPARQL queries run at once;
- OAuth discovery answers 404, so a client connects without starting an OAuth flow.

Anyone with the URL can search the corpus, so turn it off (`IKG_MCP_PUBLIC=off`, restart) when the
test is over. Checked 2026-11-10: ChatGPT connected through a quick tunnel with *No authentication*,
and the read-only tools worked in a quick test.

**ChatGPT plugin, the three test questions, 2026-11-11** (public mode through a quick tunnel; the
model was ChatGPT's own). The call log shows one `ikg_search_passages` per question and one follow-up
search for `upa`, and no `ikg_get_passage`.

| # | Result |
|---|---|
| upa | Right content (the Self unborn, undying, beyond argument). Cited "Katha Upanishad 1.2.9, 2.2.18, 2.2.19, 2.3.12": no passage ids, no translator, and a verse numbering that is not Paramananda's (the corpus has `upa.katha.p2.v18`). The search it ran returned mostly Paramananda's *commentary*. It also added outside web sources |
| xw | Bhagiratha, and Shiva receiving Ganga in his locks. Cited Ramayana 1.44 (Griffith), Mahabharata 3.108 and 13.26 (Ganguli): the right works, sections and translators, but no passage ids, and one merged story rather than each telling separately |
| adv | Abstained correctly: Hanuman isn't in the Katha Upanishad; the dialogue is Yama's with Nachiketas |

**What differed from Claude.** The abstention held, but ChatGPT didn't cite `passage_id`s or keep the
two Ganga tellings apart. Both rules are in the server's instructions, which ChatGPT seems not to pass
to its model. The tool descriptions, which it does read, don't repeat the citation rule.

## The three test questions

docs/08 asks for the same three questions on every platform, citing the same passages, with the
unanswerable one abstaining. Claude Code, 2026-11-07, with the plugin's skill loaded through
`--plugin-dir` and the `ikg` server over **stdio** on the local build (hybrid retrieval).

What this tests: the skill, the server's instructions and tools, and the answers. What it doesn't:
the plugin's own HTTP connection (`.mcp.json` with the URL and token substituted from the plugin
settings), because `--plugin-dir` doesn't prompt for plugin settings. That path is checked by
`claude plugin validate` and by the server's bearer-token tests, and the same server answered over
HTTP with the token in the Docker check (docs/08 B1). The HTTP run below covers the transport and
the token; an installed-plugin run is still to do.

| # | Question | Expected | Result |
|---|---|---|---|
| upa | What does Yama teach Nachiketas about the Self? | Katha Upanishad 2.18–2.20 and nearby | Cited `upa.katha.p2.v18`, `p2.v20`, `p2.v23`, `p4.v01` and more, translator named, *ātman* for "Self" (Opus 5.5, $0.61) |
| xw | How did the Ganga descend from heaven, and who received her on his head? | Ramayana 1.43–44 and Mahabharata 3.108–109 | Both works, each telling separately: `ram.b1.c043.p03`, `ram.b1.c044.p02`, `mbh.b03.s108.p04`, `mbh.b03.s109.p01` (Haiku 5.5, $0.045) |
| adv | What does Hanuman say to Nachiketas in the Katha Upanishad? | Abstain: Hanuman isn't in it | Searched, said Hanuman doesn't appear, corrected the premise, offered Yama's teaching instead (Haiku 5.5, $0.04) |

**Over HTTP, 2026-11-07.** The same three questions, headless (`claude -p`, Haiku 5.5), with the
skill through `--plugin-dir` and the server as the Docker `mcp` service at
`http://127.0.0.1:8001/mcp`, configured exactly like the plugin's `.mcp.json` (Streamable HTTP,
`Authorization: Bearer <IKG_MCP_TOKEN>`) through `--strict-mcp-config`. Without the token the
server answers 401; with it, 200. The three runs made 28 requests to the container.

| # | Result |
|---|---|
| upa | `upa.katha.p2.v18`, `p2.v20`, `p2.v23`, `p3.v03`, `p4.v01`, `p6.v18`; Paramananda named, *ātman* for "Self"; noted it read verses, not commentary ($0.40) |
| xw | Ramayana 1.43–44 and Mahabharata 3.108 (`ram.b1.c043.p03`, `ram.b1.c044.p02`, `mbh.b03.s108.p04`; 3.109 not recorded) plus `mbh.b13.s026.p09` and two Vishnu Purana passages, each work kept separate. One slip: a heading says "Part 2, section 8" over passages from sections 2 and 8; the ids are right ($0.60) |
| adv | Abstained: Hanuman isn't in the Katha Upanishad; cited `upa.katha.p1.v01`, then where Hanuman does appear (`mbh.b03.s146.p01`, `ram.b6.c128.p01`) ($0.37) |

The costs are higher than the stdio runs above because each headless run starts with an empty
prompt cache. Still not covered: the `${user_config.*}` substitution in an installed plugin, which
only an interactive `/plugin install` exercises.

**What we learned.** The first runs of the `xw` question cited only the Mahabharata and gave
references without passage ids: the model never opened the skill and guessed `works:
["mahabharata"]`. Whether a client loads a skill is up to its model, but every MCP client reads the
server's instructions and tool descriptions. So the essential rules now live there too (cite
`passage_id`; for stories told in several works, search all or once per work), and the skill's
description says to load it before calling any `ikg` tool. After that, Haiku loaded the skill,
searched across works and cited all four expected sections. This matters for ChatGPT and Copilot,
which won't have the skill at all.

## ChatGPT: a custom GPT with Actions

The GPT calls the web app's JSON API, not the MCP server: `searchPassages`, `getPassage`,
`getEntity`, `askQuestion` (and `getGraph`). The app publishes them at `/openapi.json`, with only the
`/api/*` routes, request and response schemas, and a `servers` entry from `IKG_PUBLIC_URL`.

**The server.** In `.env`: `IKG_API_TOKEN` (16+ random characters) and `IKG_PUBLIC_URL` (the app's
public `https://` address). Search, passages and entities are public and read-only; the token is for
`askQuestion`: with it, Ask skips the same-origin check and counts against its own hourly limit
(`IKG_API_ASK_LIMIT`, default 60), still within the daily budget. A wrong token gets 401, and so does
a non-browser client without one once the token is set. Without `IKG_PUBLIC_URL`, `/openapi.json` has
no `servers` entry and the GPT builder asks for one. For a quick
public address, a Cloudflare quick tunnel: `cloudflared tunnel --url http://127.0.0.1:8000` (a new
random address on every start, so `IKG_PUBLIC_URL` and the GPT's schema must follow it; a named
tunnel keeps one address).

**The GPT** (ChatGPT, a plan that can create GPTs; menu names as of November 2026):

1. *Explore GPTs → Create → Configure.* Name it, and paste [`chatgpt/instructions.md`](chatgpt/instructions.md)
   into *Instructions*. Conversation starters: the three test questions above.
2. *Actions → Create new action → Import from URL:* `<IKG_PUBLIC_URL>/openapi.json`.
3. *Authentication → API Key → Bearer*, with `IKG_API_TOKEN`.
4. A *Privacy policy* URL is required only to share the GPT publicly.

The instructions tell the GPT to write answers itself from `searchPassages` and `getPassage` and
to call `askQuestion` only when asked: the server's model is whatever `IKG_ANSWER_MODEL` says, and a
small local model abstains or slips more often than a GPT reading the passages itself.

**Checked so far** (2026-11-07, through a quick tunnel): `/openapi.json` lists the five operations
with the public `servers` URL; search, passage and entity answer without a token; Ask answers with
the token in about 2 s on the local model and refuses a wrong one (401); the admin pages redirect to
the login. The three test questions in a GPT are still to do, and need a ChatGPT account.

