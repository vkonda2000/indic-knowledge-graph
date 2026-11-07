# 09 Related projects: the Sanskrit side, and what to take from it

**Status:** proposed, 2026-11-07. Separate from [08 Agentic extension](08-agentic-extension.md), which
continues first. A two-page summary for people outside the code is in
[`briefs/ikg-brief.pdf`](briefs/ikg-brief.pdf).

## 0. Why

IKG works on public-domain **English translations**. Its weakest results are where the question uses
the **Sanskrit** word: with hybrid retrieval, 11 of 15 `skt-` items still miss (06 §1), and dense
search alone finds 1 of 15, because an English embedding model doesn't know that *krodha* is
anger. The gazetteer bridges some of that gap by hand.

Five projects, looked at on 2026-11-07, work on the Sanskrit side. None of them does what IKG does:
cited question answering over sources, with edition, translator and kind kept apart. So they are
complements, not competitors. Each offers something specific.

| Project | What it is | Licence, as published | What IKG could take |
|---|---|---|---|
| [samskritam.ai](https://www.samskritam.ai/) (Sriram Subramanian) | Sanskrit reader with word-by-word analysis and sūtra derivations; a rule-graded grammar benchmark for AI models | Its sources: Digital Corpus of Sanskrit (DCS) **CC BY 4.0**; Vidyut from Ambuda. Its own code and data: not stated | DCS as the Sanskrit original; Vidyut for analysis; the rule-graded benchmark design |
| [IISc Sanskrit speech](https://prathosh.in/sanskrit-complinguistics.html) (Prathosh A.P.) | Chanted TTS (Vāgdhenu), Sanskrit ASR (Su-śrotā), learning and reading apps; open weights on Hugging Face | Not stated | Recited verses, voice questions |
| [Sarvamūla](https://sarvamulavani.com/) (same group) | Sanskrit reader with audio for 37 works, almost certainly Madhvācārya's *Sarvamūla* (the group's page names his *Mahābhārata Tātparya Nirṇaya*) | Not stated; no edition named | A school of thought (Dvaita) on IKG's own texts |
| [Siddhāntakośa: śāstra as a rulebase](https://siddhantakosha.org/initiatives/shaastra-as-a-rulebase/) | A proposal to turn śāstras into condition–action rules, piloted on Āyurveda, linked by Tantrayukti relations | Nothing released | Relation vocabulary; a possible collaborator |
| [Dharmamitra](https://dharmamitra.org/translate) (UC Berkeley BAIR, Tohoku, Tsadra Foundation) | MT from Sanskrit, Pāli, Tibetan, Chinese into English; OCR; MITRA-lexicon of attested translations; open models (Gemma-2 MITRA, ByT5-Sanskrit) | Weights and code published; licence to be read on each model page | The lexicon for renderings; ByT5-Sanskrit for analysis; MT drafts, labelled |

## 1. Rules for anything taken from outside

1. **Machine translation is never a source.** It may sit beside a passage, labelled as machine
   translation, the way commentary is labelled. An answer cites only published text (ONBOARDING §3,
   rules 1–2). samskritam.ai's verse translations and Dharmamitra's output are both MT.
2. **Licence before ingestion.** Nothing enters `data/` without a stated licence that allows it, or
   the owner's written permission, recorded in the source registry. Only DCS states one today.
3. **Attribute everything.** External data carries its origin (project, version, licence) in the
   manifest and the RDF, like editions today.
4. **Measure before adopting** (rule 5). A change that touches retrieval or answers comes with
   `ikg eval` before and after, as hybrid search did.

## 2. Workstreams

### S1. The Sanskrit original, aligned to the translations (highest value)

- **Data:** DCS text and its word-level annotation (lemma, morphology), CC BY 4.0. Check first which of
  the four works and which recensions it covers.
- **Model:** a Sanskrit edition of each work (`ikg:Edition` with language `sa`; D10 already separates
  text and translation), with passages and locators of its own.
- **Alignment:** a `translationOf` link from each English passage to the Sanskrit span it translates.
  Start with the Upanishads, where Paramananda numbers verses as the text does, so alignment is
  verse to verse. The Mahābhārata is harder: Ganguli follows the vulgate's section numbering and DCS
  may follow another, so alignment there is at section level, and only where it can be checked.
- **Gazetteer:** a `lemmas` field per concept (the DCS lemma for *dharma*, *krodha*, *ātman*), so a
  Sanskrit word in a question matches the aligned Sanskrit passage, and through it the translation.
  Vidyut or ByT5-Sanskrit can analyse a question's Sanskrit word into its lemma.
- **Retrieval:** a lemma match becomes a third signal in hybrid fusion. The answer still cites the
  English passage; the Sanskrit is shown beside it.
- **Done when:** the Upanishads are aligned verse by verse, and `ikg eval` shows the effect on `skt-`
  and `upa-` items, kept only if it helps.

### S2. Renderings checked against attested translations

- MITRA-lexicon lists how translators have rendered each Sanskrit word in English. Compare it with the
  gazetteer's renderings, and propose missing ones to curators. Nothing is added automatically.
- It also helps roadmap item L6, rendering precision: a rendering no translator attests for a concept
  is a candidate to drop.
- **Done when:** a report of gazetteer renderings that are attested, unattested and missing for the
  75 entities, with curator decisions recorded in the provenance log.

### S3. A school of thought: Madhvācārya's commentaries

- The ontology already has `SchoolOfThought`, `Claim`, `heldBy`. Madhva's commentaries on the
  Upanishads, the Gītā and the Mahābhārata are the first real test of it.
- **Step 1, link out:** from a verse to the corresponding commentary on Sarvamūla, with no text copied.
- **Step 2, with permission:** ingest as `kind=commentary`, attributed to Madhvācārya and the Dvaita
  school, never blended with the text or with other schools.
- **Done when:** step 1 ships; step 2 waits for written permission and an edition to cite.

### S4. Audio

- A "hear this verse" link per cited passage, once the Sanskrit original is aligned (S1); voice
  questions through Su-śrotā ASR, transcribed and shown before searching.
- Licences of Vāgdhenu and Su-śrotā to be confirmed with the author first.

### S5. Rules and relations

- No code to use yet. Siddhāntakośa's Tantrayukti relations are a candidate vocabulary for typed
  relations between passages and claims, the step beyond co-occurrence (D5). Contact, and revisit
  when they publish.

## 3. Order and size

| # | Item | Depends on | Size |
|---|---|---|---|
| 1 | S1 for the Upanishads (DCS, verse alignment, lemmas, eval) | DCS coverage check | M |
| 2 | S2 rendering report | MITRA-lexicon access and licence | S |
| 3 | S3 step 1, link-outs | – | S |
| 4 | S1 for the Gītā, then the Mahābhārata at section level | 1 | M–L |
| 5 | S4 audio | 1, licences | M |
| 6 | S3 step 2 | permission | M |
| – | S5 | their release | – |

## 4. Open questions

- Which works and recensions does DCS cover, and does its Mahābhārata numbering map to Ganguli's?
- What licences apply to Dharmamitra's models and lexicon, and to the IISc models? (Their pages state
  none we could find.)
- Who to ask at Sarvamūla, and which printed edition its texts follow.
- Whether samskritam.ai's benchmark items could include IKG-style "what does this verse say" checks,
  shared both ways.

Sources: the five sites above, the
[Dharmamitra model guide](https://dharmamitra.github.io/dharmamitra-guides/mitra_models), and the papers on
[Mitrasaṃgraha](https://arxiv.org/html/2601.07314v1) and
[MITRA-lexicon](https://www.opentrain.ai/papers/mitra-lexicon-lexica-of-attested-translations-from-sanskrit-into-english-tibetan--arxiv-2610.25934/),
as published on 2026-11-07.
