import pytest

from ikg import config

from ikg.entities import Entity, Gazetteer
from ikg.segment import Passage
from ikg.sources import Work


@pytest.fixture(autouse=True)
def _no_real_embeddings(tmp_path, monkeypatch):
    """Whatever IKG_EMBED or IKG_VECTOR_STORE say in the shell or .env, tests never load an embedding
    model or use a configured Postgres, and a build
    in a test never touches the developer's data/processed embeddings."""
    monkeypatch.setattr(config, "EMBED", False)
    monkeypatch.setattr(config, "VECTOR_STORE", "file")  # never a developer's Postgres
    monkeypatch.setattr(config, "EMBEDDINGS_FILE", tmp_path / "_embeddings" / "embeddings.npy")
    monkeypatch.setattr(config, "EMBEDDINGS_META_FILE", tmp_path / "_embeddings" / "embeddings.json")


def make_work(work_id: str, parser: str | None = None) -> Work:
    return Work(id=work_id, title=work_id, translator="T", year="1900", parser=parser or work_id)


def make_passage(pid: str, work: str, text: str, kind: str = "narrative") -> Passage:
    return Passage(id=pid, work=work, book="1", book_label="Book 1", section=1,
                   section_label="Section 1", ordinal=1, kind=kind, text=text,
                   translator="T", source_url="https://example.org")


@pytest.fixture
def gazetteer() -> Gazetteer:
    return Gazetteer([
        Entity(id="dharma", type="Concept", label="Dharma", aliases=["dharma"],
               renderings=["righteousness", "duty"]),
        Entity(id="atman", type="Concept", label="Atman", aliases=["atman"], aliases_cs=["Self"]),
        Entity(id="krishna", type="Person", label="Krishna", aliases=["krishna", "kesava"]),
        Entity(id="vyasa", type="Person", label="Vyasa", aliases=["vyasa", "krishna-dwaipayana"]),
        Entity(id="arjuna", type="Person", label="Arjuna", aliases=["arjuna", "partha"]),
        Entity(id="bharata_ram", type="Person", label="Bharata", aliases=["bharata"], works=["ramayana"]),
        Entity(id="rama", type="Person", label="Rama", aliases=["dasarathi"],
               aliases_by_work={"ramayana": ["rama"]}),
        Entity(id="sita", type="Person", label="Sita", aliases=["sita"]),
    ])
