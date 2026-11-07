---
name: ikg-scholar
description: Load before calling any ikg MCP tool (ikg_search_passages, ikg_get_passage, ikg_get_entity, ikg_sparql_query, ikg_ask). Covers questions about the Upanishads, Mahabharata (including the Bhagavad Gita), Ramayana or Vishnu Purana, the people, gods, places and concepts in them (Krishna, Arjuna, Sita, Ganga, dharma, atman, moksha...), and how translators rendered Sanskrit terms. Explains how to search across works, cite passage ids and translators, keep text and commentary apart, and abstain, so answers come from the passages instead of memory.
---

# Answering from the Indic Knowledge Graph

The `ikg` MCP server holds public-domain English translations of four works, split into citable
passages, plus a knowledge graph of the people, places and concepts in them:

| Work id | Text | Translation |
|---|---|---|
| `upanishads` | Isa, Katha, Kena Upanishads | Swami Paramananda, 1919, with his commentary |
| `mahabharata` | all 18 books, including the Bhagavad Gita (Bhishma Parva) | Kisari Mohan Ganguli, 1883–96 |
| `ramayana` | books 1–6 | Ralph T. H. Griffith, 1870–74, in verse |
| `vishnu_purana` | parts 1–6 | M. N. Dutt after H. H. Wilson, 1896 |

The point of using it is that every claim can be checked. Answer from the passages the tools
return, not from what you remember about these texts, even when you are sure.

## The rules

1. **Cite every claim with its passage id.** Each sentence that states what a text says gets the
   passage it comes from: its `ref` **and** its `passage_id`, e.g. *(Katha Upanishad, Part 2,
   Verse 18, tr. Paramananda; `upa.katha.p2.v18`)*. The id is what lets a reader open the exact
   passage, so a reference without it is incomplete. Name the translator. No passage, no claim.
2. **Primary text is not commentary.** Every passage has a `kind`. `verse` and `narrative` are the
   translated text itself. `commentary` and `introduction` are the translator's own explanation;
   `secondary` is modern scholarship. Write "Paramananda comments that…", never "the Upanishad
   says…", for a commentary passage. When the user asks what the text says, search with
   `primary_only: true`.
3. **The English word is not the Sanskrit concept.** "Righteousness" and "duty" are Ganguli's
   renderings of *dharma*; "Self" is Paramananda's of *ātman*. You may name the Sanskrit term, but
   don't attribute the translator's wording to the original.
4. **Keep works and speakers apart.** If Bhishma says one thing in the Santi Parva and Krishna
   another in the Gita, report them separately. Many stories are told in more than one work (the
   descent of the Ganga, Parashurama, Rama's story, Savitri): unless the user names a work, check
   each work that might tell it and report each telling under its own heading, without blending them.
5. **Say when the sources don't answer.** If the passages don't support an answer, say so plainly
   and say what you looked for. `ikg_ask` replies `NOT_IN_SOURCES` in that case: pass that on, don't
   fill the gap from memory. A false premise ("Hanuman in the Katha Upanishad") gets a correction,
   not an invented answer.
6. **These are 19th-century translations,** sometimes abridged (Griffith omits cantos and says so).
   Don't present them as the Sanskrit itself.

## Which tool when

| Need | Tool |
|---|---|
| Find what the texts say about something | `ikg_search_passages` (start here; `k` up to 20) |
| Read a passage in full, or what comes before and after | `ikg_get_passage` with `neighbours: 1` or `2` |
| Who or what someone is, their other names, how translators render a concept | `ikg_get_entity` (accepts any alias: "Partha" finds Arjuna) |
| Counts, lists or comparisons across the corpus | `ikg_sparql_query` (read [references/sparql.md](references/sparql.md) first) |
| A quick cited answer the server writes for you | `ikg_ask` (rate-limited and budgeted; prefer writing the answer yourself from searches) |

A good answer usually takes two to four calls:

1. `ikg_search_passages` with the question as asked. Use `works` when the user names a text, and
   `primary_only: true` when they ask what the text itself says. For a story or figure that may
   appear in several works, also search once per work (`works: ["ramayana"]`, `["mahabharata"]`,
   ...): one work's passages can crowd the others out of a single search.
2. If the top passages are close but not decisive, search again with the names or terms the first
   results used (the translator's spelling, the speaker's name), or read neighbours with
   `ikg_get_passage`: a speech often runs over several passages.
3. For a person or concept, `ikg_get_entity` gives aliases and renderings, so you can search for
   the words the translator actually used.
4. Write the answer: a few sentences, each cited, quoting short phrases where wording matters.
   Group by work or speaker when there is more than one.

Search results report `retriever` (`hybrid` combines keyword and meaning search) and recognised
`entities`. A `score` only ranks results within one search; don't report it.

## Answer shape

Every citation carries its `passage_id`, and every quotation is copied from the passage text the
tools returned, word for word. (This example was checked against the build; for a real answer use
what the tools return, not this.)

> In the Katha Upanishad, Yama tells Nachiketas that "this Self is never born, nor does It die"
> (Part 2, Verse 18, tr. Swami Paramananda; `upa.katha.p2.v18`), and that it "dwells in the heart
> of each living being" (Part 2, Verse 20; `upa.katha.p2.v20`). Paramananda's commentary on that
> verse explains that the Atman is "not perceived by ordinary mortals because of Its subtlety"
> (commentary on 2.20; `upa.katha.p2.v20.c1`). The translator's "Self" renders *ātman*.

For a question the texts don't answer:

> I couldn't find this in the four texts IKG holds. Searching for Hanuman in the Upanishads
> returned only passages about Hanuman from the Ramayana and Mahabharata; the Katha Upanishad
> doesn't mention him.

More worked examples: [references/examples.md](references/examples.md).

## Limits to keep in mind

- The corpus is four works in English translation. Other Puranas, the Vedas and the Sanskrit
  originals are not in it.
- Entity links are co-occurrence: `connections` in `ikg_get_entity` mean two names appear in the
  same passages, not that the texts state a relation.
- Retrieval is good, not perfect: the right passage is in the top 8 about two-thirds of the time.
  If a first search misses, rephrase before concluding the texts are silent.
