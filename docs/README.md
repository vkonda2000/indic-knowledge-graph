# Documentation

Specification, architecture and design of the Indic Knowledge Graph, reverse-engineered from
the code as of October 2026. Where these documents and the code disagree, the code is right
and the document is out of date. Please fix the document in the same pull request.

| Document | Read it to learn |
|---|---|
| [01 Specification](01-specification.md) | What the system is for, who uses it, what it must do (functional and non-functional requirements), and what is in and out of scope today |
| [02 Architecture](02-architecture.md) | The components, how data flows through the build and at query time, where state lives, how it is deployed and tested in CI, the key decisions behind it, and the full technology stack with versions (§7) |
| [03 Design](03-design.md) | How each module works: segmentation, entity extraction, retrieval scoring, cited answering, graph and RDF export, web app, upload screening, evaluation |
| [04 Data model](04-data-model.md) | The ontology, every on-disk file format, identifier schemes, and how the source and gazetteer registries are structured |
| [05 Interfaces](05-interfaces.md) | Every HTTP route, JSON API, CLI command and environment variable |
| [06 Roadmap](06-roadmap.md) | Measured limitations of the current system, a done / partial / not-started checklist against the target with candidate use cases, the project's original contributions, and a phased plan toward the target system, with extension points for each change |
| [07 Target alignment](07-target-alignment.md) | The target (an ontology-based civilizational knowledge system), a capability-by-capability gap analysis, the SOT-fidelity definition, and the ontology extensions and constraints the target adds |

Slides: [`slides/ikg-project-goals.pptx`](slides/ikg-project-goals.pptx) is an 8-slide overview
of the project's goals and high-level objectives, current status and roadmap, with speaker
notes, for introducing the project to collaborators and subject experts. Its figures are a
snapshot from October 2026; the documents above are authoritative.

Reviews: [`reviews/`](reviews/) holds the independent Fable reviews of code and architecture
(ONBOARDING §7). Their findings are tracked as limitations in 06 §2.

**Scope.** The project's scope is the target system described in [01 §0](01-specification.md#0-target-system-and-this-prototype).
The code here is a working prototype of its core on a public-domain corpus.

If you are new to the project, read 01, then 07, then 02. Go to 03 and 04 when you change a
module, and to 06 when you plan new work.

## Conventions

- File references are relative to the repository root, for example `src/ikg/retrieve.py`.
- Requirement identifiers (`FR-…`, `NFR-…`) are defined in 01 and cited elsewhere.
- Numbers come from the current build of the four bundled works unless stated otherwise:
  14,617 passages, 66,411 entity mentions, 75 entities in the graph, 1,903 co-occurrence edges.
- Diagrams are Mermaid and render on GitHub.
