# 04 Data model

## 1. Conceptual model

```mermaid
erDiagram
    WORK ||--o{ EDITION : "editionOf"
    EDITION ||--o{ BOOK : "fromEdition"
    WORK ||--o{ BOOK : "partOf"
    BOOK ||--o{ SECTION : "partOf (implicit)"
    SECTION ||--o{ PASSAGE : "partOf"
    PASSAGE ||--o{ MENTION : "contains"
    ENTITY ||--o{ MENTION : "entity"
    CONCEPT ||--o{ RENDERING : "renders"
    ENTITY }o--o{ ENTITY : "coOccursWith (weighted)"
    CLAIM }o--|| ENTITY : "statedBy / about (planned)"
    CLAIM }o--o| SCHOOL : "heldBy (planned)"
    CLAIM }o--o{ PASSAGE : "groundedIn (planned)"
```

- **Passage kinds** are subclasses of `ikg:Passage`. `Verse` and `Narrative` are primary text;
  `Commentary`, `Introduction` and `Secondary` are not.
- **Entity types** are subclasses of `ikg:Entity`: `Person`, `Deity`, `Place`, `Concept`,
  `SchoolOfThought`.
- A `Rendering` is an English word that an edition uses for a Sanskrit `Concept`. It is a
  separate class, so translation choices are data rather than being merged into the concept.
- A `Mention` records *via* `alias` or `rendering`.
- `Claim`, `statedBy`, `heldBy`, `groundedIn` and `about` are reserved for curated propositions
  ("Yama teaches that the atman is unborn", stated by Yama, grounded in `upa.katha.p1.v18`).
  The ontology defines them but nothing generates them yet.

The full ontology is in `ontology/ikg.ttl` (OWL, 96 lines).

## 2. Identifiers

| Thing | Format | Example |
|---|---|---|
| Work | slug, from the registry | `mahabharata`, `gita_notes` |
| Mahabharata passage | `mbh.b<book:02>.s<section:03>.p<chunk:02>` | `mbh.b05.s033.p02` |
| Ramayana passage | `ram.b<book>.c<canto:03>.p<chunk:02>` | `ram.b4.c012.p01` |
| Upanishad verse | `upa.<name>.p<part>.v<verse:02>` | `upa.katha.p1.v19` |
| … its commentary | `<verse id>.c<n>` | `upa.katha.p1.v19.c1` |
| … introduction / preamble | `upa.<name>.intro<nn>`, `upa.<name>.p<part>.pre<nn>` | `upa.kena.intro01` |
| … peace chant | `upa.<name>.peace`, `.peace-close` | `upa.isa.peace` |
| Vishnu Purana passage | `vp.b<part>.s<section:03>.p<chunk:02>` | `vp.b1.s012.p01` |
| Generic (added) passage | `<work>.f<file>.b<book>.s<section:03>.p<chunk:02>` | `gita_notes.f1.b1.s002.p01` |
| Entity | gazetteer `id` | `krishna`, `dharma`, `bharata_ram` |
| RDF instance | `https://example.org/ikg/data/<kind>/<id>` | `d:entity/krishna`, `d:book/mahabharata/05` |

IDs are hierarchical, so a prefix selects a unit: `mbh.b12` is the Shanti Parva and
`upa.katha` is the Katha Upanishad. The evaluation set relies on this.

## 3. Files on disk

All paths are under `IKG_DATA` (default `data/`) unless noted.

| Path | Format | Written by | Read by | In git |
|---|---|---|---|---|
| `src/ikg/data/sources.yaml` | YAML | developers | `sources` | yes |
| `src/ikg/data/gazetteer.yaml` | YAML | developers | `entities` (fallback) | yes |
| `ontology/ikg.ttl` | Turtle/OWL | developers | people, SPARQL tools | yes |
| `eval/questions.jsonl` | JSONL | developers | `evaluate` | yes |
| `works.yaml` | YAML | web admin / contribute | `sources` | no |
| `gazetteer.yaml` | YAML | web admin gazetteer editor | `entities` (takes precedence) | no |
| `raw/pg<n>.txt` | Gutenberg text | `fetch` | `segment` | no |
| `uploads/<uuid32>.<txt\|md\|pdf>` | as uploaded | web uploads | `segment` | no |
| `processed/passages.jsonl` | JSONL, one `Passage` per line | `build` | `ikg stats`, `analyze` | no |
| `processed/mentions.jsonl` | JSONL, one `Mention` per line | `build` | `Runtime`, `analyze` | no |
| `processed/bm25.pkl` | Python pickle of `BM25Index` (with passages) | `build` | `Runtime` | no |
| `processed/cooccurrence.graphml` | GraphML | `build` | `Runtime` | no |
| `processed/ikg.ttl` | Turtle | `build` | `ikg sparql` | no |
| `reports/corpus-analysis.md` | Markdown | `analyze` | people | yes |
| `eval/results/run-*.json` | JSON | `eval` | people | no |

`bm25.pkl` is a pickle. Only load files this application wrote itself; never accept one from
elsewhere.

## 4. Record schemas

### Passage (`passages.jsonl`)

```json
{"id": "upa.katha.p1.v19", "work": "upanishads", "book": "katha", "book_label": "Katha Upanishad",
 "section": 1, "section_label": "Part 1, Verse 19", "ordinal": 19, "kind": "verse",
 "text": "O Nachiketas, this is thy fire that leads to heaven...", "translator": "Swami Paramananda",
 "source_url": "https://www.gutenberg.org/ebooks/3283", "work_title": "Upanishads"}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | Unique within the corpus; the build fails on duplicates |
| `work` | string | Work ID |
| `book`, `book_label` | string | Top-level unit: parva, kanda, Upanishad, Purana part, or generic level 1 |
| `section`, `section_label` | int, string | Second-level unit |
| `ordinal` | int | Chunk number within the section (the verse number for Upanishad verses) |
| `kind` | enum | `verse`, `narrative`, `commentary`, `introduction`, `secondary` |
| `text` | string | At most ~1,500 characters |
| `translator` | string | From the work registry: translator or author |
| `source_url` | string | Gutenberg ebook URL or `upload:<original name>` |
| `work_title` | string | Display title |

### Mention (`mentions.jsonl`)

```json
{"entity": "dharma", "passage": "mbh.b12.s059.p03", "work": "mahabharata", "surface": "righteousness", "via": "rendering"}
```

### Work registry entry (`sources.yaml`, `works.yaml`)

```yaml
- id: gita_notes                 # unique slug
  title: Gita notes
  translator: A. Scholar         # translator or author
  year: "2024"
  parser: generic                # mahabharata | ramayana | upanishads | vishnu_purana | generic
  files:
    - upload: 3f2a...c9.md       # or: gutenberg: 15474
      original_name: notes.md
  notes: ""                      # contributor's notes to the reviewer
  status: pending                # pending | approved | rejected (bundled works: approved)
  kind: secondary                # passage kind used by the generic parser
  submitted_by: someone@example.org
  submitted_at: "2026-10-23 15:40"
  screening: "qwen3.5:9B: It discusses Krishna and dharma."   # uploads only
```

### Gazetteer entry

```yaml
- id: dharma                     # stable, used in graph and RDF
  type: Concept                  # Person | Deity | Place | Concept | SchoolOfThought
  label: Dharma
  iast: dharma                   # optional
  aliases: [dharma]              # case-insensitive
  aliases_cs: []                 # case-sensitive (e.g. [Self] for atman)
  renderings: [righteousness, duty, duties]   # translator's English words
  works: []                      # optional: restrict all forms to these works
  aliases_by_work: {}            # optional: {ramayana: [rama]}
```

Unknown fields and duplicate IDs are rejected when the gazetteer is loaded.

### Graph (GraphML)

| Element | Attributes |
|---|---|
| Node (entity ID) | `type`, `label`, `mentions` (int), `works` (`"work:count;work:count"`) |
| Edge | `weight` (shared passages), `weight_<work>` (shared passages in that work) |

### Evaluation item (`eval/questions.jsonl`)

```json
{"id": "mbh-04", "type": "retrieval", "question": "How did Karna die when the wheel of his chariot sank into the earth?", "expected": ["mbh.b08"]}
{"id": "adv-02", "type": "adversarial", "question": "What does the Kena Upanishad say about the battle of Kurukshetra?", "expected": [], "works": ["upanishads"]}
```

## 5. Example SPARQL

```sparql
# Translator renderings of each concept
SELECT ?concept ?word WHERE {
  ?r a ikg:Rendering ; ikg:renders ?c ; ikg:surface ?word .
  ?c rdfs:label ?concept .
} ORDER BY ?concept
```

```sparql
# Books where Dharma is mentioned through a rendering, most first
SELECT ?book ?n WHERE {
  ?m a ikg:Mention ; ikg:entity <https://example.org/ikg/data/entity/dharma> ;
     ikg:via "rendering" ; ikg:inUnit ?b ; ikg:count ?n .
  ?b rdfs:label ?book .
} ORDER BY DESC(?n)
```

Top results: Book 12 (Santi Parva) 1,438, Book 13 (Anusasana Parva) 671.

`ikg sparql` adds the `ikg:` and `rdfs:` prefixes automatically when a query does not start
with `PREFIX`. Instance IRIs contain `/`, which is not allowed in a SPARQL prefixed name, so
write them in full as above.
