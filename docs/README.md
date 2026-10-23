# Documentation

Specification, architecture and design of the Indic Knowledge Graph, reverse-engineered from
the code as of October 2026. Where these documents and the code disagree, the code is right
and the document is out of date. Please fix the document in the same pull request.

| Document | Read it to learn |
|---|---|
| [01 Specification](01-specification.md) | What the system is for, who uses it, what it must do (functional and non-functional requirements), and what is in and out of scope today |
| [02 Architecture](02-architecture.md) | The components, how data flows through the build and at query time, where state lives, how it is deployed, and the key decisions behind it |
| [03 Design](03-design.md) | How each module works: segmentation, entity extraction, retrieval scoring, cited answering, graph and RDF export, web app, upload screening, evaluation |
| [04 Data model](04-data-model.md) | The ontology, every on-disk file format, identifier schemes, and how the source and gazetteer registries are structured |
| [05 Interfaces](05-interfaces.md) | Every HTTP route, JSON API, CLI command and environment variable |
| [06 Roadmap](06-roadmap.md) | Measured limitations of the current system and a phased plan for extending it, with extension points for each change |

If you are new to the project, read 01, then 02. Go to 03 and 04 when you change a module,
and to 06 when you plan new work.

## Conventions

- File references are relative to the repository root, for example `src/ikg/retrieve.py`.
- Requirement identifiers (`FR-…`, `NFR-…`) are defined in 01 and cited elsewhere.
- Numbers come from the current build of the four bundled works unless stated otherwise:
  15,257 passages, 68,901 entity mentions, 75 entities in the graph, 1,921 co-occurrence edges.
- Diagrams are Mermaid and render on GitHub.
