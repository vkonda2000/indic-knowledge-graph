# SPARQL over the IKG graph

`ikg_sparql_query` runs read-only SELECT or ASK queries. The prefixes `ikg:`, `rdf:` and `rdfs:` are
added when a query declares none. For the full ontology, read the MCP resource `ikg://ontology`.

## What's in the graph

| Class | Meaning | Useful properties |
|---|---|---|
| `ikg:Work`, `ikg:Edition` | A text and one translation of it | `rdfs:label`, `ikg:editionOf`, `ikg:translator`, `ikg:year`, `ikg:sourceUrl` |
| `ikg:Book`, `ikg:Section` | Structural units (parva, kanda, Upanishad part...) | `rdfs:label`, `ikg:partOf` |
| `ikg:Person`, `ikg:Deity`, `ikg:Place`, `ikg:Concept`, `ikg:SchoolOfThought` | Entities (all `ikg:Entity`) | `rdfs:label`, `ikg:iast`, `ikg:alias` |
| `ikg:Mention` | How often an entity is mentioned in a unit, and how | `ikg:entity`, `ikg:inUnit`, `ikg:count`, `ikg:via` ("alias" or "rendering") |
| `ikg:Rendering` | An English word an edition uses for a Sanskrit concept | `ikg:renders`, `ikg:surface`, `ikg:fromEdition`, `ikg:count` |
| `ikg:Claim` | Expert-curated assertions (few or none yet) | `ikg:statedBy`, `ikg:heldBy`, `ikg:groundedIn`, `ikg:about` |

Passages themselves are usually not in the graph; use `ikg_search_passages` and
`ikg_get_passage` for text. Entity IRIs contain `/`, so write them in full:
`<https://example.org/ikg/data/entity/dharma>`.

## Examples

How each translation renders each concept, most used first:

```sparql
SELECT ?concept ?edition ?word ?n WHERE {
  ?r a ikg:Rendering ; ikg:renders ?c ; ikg:surface ?word ; ikg:fromEdition ?e ; ikg:count ?n .
  ?c rdfs:label ?concept . ?e rdfs:label ?edition .
} ORDER BY ?concept DESC(?n)
```

Books where *dharma* appears through a translator's rendering, most first:

```sparql
SELECT ?book ?n WHERE {
  ?m a ikg:Mention ; ikg:entity <https://example.org/ikg/data/entity/dharma> ;
     ikg:via "rendering" ; ikg:inUnit ?b ; ikg:count ?n .
  ?b rdfs:label ?book .
} ORDER BY DESC(?n)
```

Every concept with its IAST name:

```sparql
SELECT ?label ?iast WHERE { ?c a ikg:Concept ; rdfs:label ?label . OPTIONAL { ?c ikg:iast ?iast } }
ORDER BY ?label
```

Results come back as strings, at most 500 rows (`truncated` says if there were more). A count from
SPARQL is a count of mentions matched by the gazetteer, which recognises listed names and
renderings only; report it as such.
