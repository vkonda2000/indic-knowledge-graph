# Worked examples

Each shows the calls and the shape of a good answer. Passage ids and wording come from the tool
results at the time; always use what the tools return, not these.

## What does the text say? (primary text only)

**Question:** "What does Yama teach Nachiketas about the Self?"

1. `ikg_search_passages` `{"query": "What does Yama teach Nachiketas about the Self?", "works": ["upanishads"], "primary_only": true}`
2. `ikg_get_passage` on the best verse with `neighbours: 1`, to read the teaching in context.

Answer from the verses; cite each one. If you also use Paramananda's commentary, search again
without `primary_only` and attribute it to him: "Paramananda explains...".

## A speaker's words

**Question:** "What does Kunti tell Karna about his birth?"

1. `ikg_search_passages` `{"query": "Kunti tells Karna about his birth", "works": ["mahabharata"]}`
2. The meeting is in the Udyoga Parva. Read the neighbours of the best passage: the dialogue runs
   over several passages, and Karna's reply is in the next ones.

Keep Kunti's words and Karna's reply apart, each with its own citation.

## A concept and its translations

**Question:** "How do the translators render dharma?"

1. `ikg_get_entity` `{"entity_id_or_name": "dharma"}`: its `renderings` and `surface_forms` give the
   words and counts.
2. For a per-translation breakdown, the first SPARQL example in [sparql.md](sparql.md).

Say clearly that "duty" and "righteousness" are English renderings, chosen by each translator, of
one Sanskrit concept; counts come from the gazetteer's matches.

## The same story in two works

**Question:** "How is the story of Rama told outside the Ramayana?"

1. `ikg_search_passages` `{"query": "story of Rama and Sita", "works": ["mahabharata"]}` (the
   Ramopakhyana in the Vana Parva) and `{"works": ["vishnu_purana"]}`.
2. Report each telling under its own work. Don't merge details from Griffith's Ramayana into the
   Mahabharata's version.

## A false premise

**Question:** "What does Hanuman say in the Katha Upanishad?"

1. `ikg_search_passages` `{"query": "Hanuman", "works": ["upanishads"]}`: nothing about Hanuman.
2. Answer that the Katha Upanishad doesn't mention Hanuman, that he appears in the Ramayana (and
   the Mahabharata), and offer to look there. Don't invent a passage.
