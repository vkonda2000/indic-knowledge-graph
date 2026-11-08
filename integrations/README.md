# Integrations: the IKG MCP server on AI platforms

One backend, the IKG MCP server (`ikg mcp`, docs/03 §13), packaged for each platform (docs/08 B2–B4).

| Platform | Package | Status |
|---|---|---|
| **Claude Code** | Plugin [`claude/ikg`](claude/ikg): the MCP server over HTTP and the `ikg-scholar` skill | Done; tested below |
| **Claude Desktop** | The MCP server over stdio (README) and the `ikg-scholar` skill, uploaded | Works locally today |
| **claude.ai** | A custom connector (remote MCP) and the skill | Needs a public HTTPS address and OAuth (docs/08 §E) |
| ChatGPT | A custom GPT with Actions, or an MCP connector | B3, next |
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
  OAuth (or not at all); there is no field for a static token. So it waits for a hosted server with
  OAuth (docs/08 §E and the OAuth step in B1's notes). Until then, use Desktop or Claude Code.

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
| xw | All four expected sections (`ram.b1.c043.p03`, `ram.b1.c044.p02`, `mbh.b03.s108.p04`) plus `mbh.b13.s026.p09` and two Vishnu Purana passages, each work kept separate. One slip: a heading says "Part 2, section 8" over passages from sections 2 and 8; the ids are right ($0.60) |
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
