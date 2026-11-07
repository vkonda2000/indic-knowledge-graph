"""Cited answers from a local model served by Ollama (`IKG_ANSWER_MODEL=ollama:<model>`).

Claude's API cites natively: it says which document each sentence came from. A local model does
not, so here the citation contract is enforced in code instead:

1. The model must reply in a JSON schema (Ollama's `format`): either `abstain` with what is
   missing, or a list of sentences, each with the numbers of the passages it rests on and a short
   verbatim quote from one of them.
2. Every sentence is checked. Its quote must have at least MIN_QUOTE_WORDS content words (not
   stopwords: a name alone, or "O monarch", proves nothing) and occur, as whole words, in a passage
   the sentence lists (compared case-, diacritic- and punctuation-insensitively). Only the listed
   passages that contain the quote are cited; the others are ignored. A sentence with none is
   dropped, never shown uncited.
3. If nothing survives, the answer abstains with NOT_IN_SOURCES.

What this proves, and what it does not: a cited passage really contains words the model copied,
so the citation points where the model read. It does not prove the sentence follows from that
passage, or that commentary was attributed as commentary; those rest on the prompt, as with Claude.
Passage text is put in the prompt with its delimiters neutralised, so an uploaded text cannot
close its passage and relabel what follows as primary text.

No API cost: `cost_usd` is 0.0. Tokens are Ollama's prompt and output counts.
"""

import json
import re

import requests

from ikg import config
from ikg.normalize import STOPWORDS, fold

PREFIX = "ollama:"
MIN_QUOTE_WORDS = 4  # content words (stopwords excluded) a quote needs to identify a passage

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "abstain": {"type": "boolean"},
        "missing": {"type": "string"},
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "passages": {"type": "array", "items": {"type": "integer"}},
                    "quote": {"type": "string"},
                },
                "required": ["text", "passages", "quote"],
            },
        },
    },
    "required": ["abstain", "sentences"],
}

INSTRUCTIONS = """Reply with JSON only, in this shape:
{"abstain": false, "missing": "", "sentences": [{"text": "...", "passages": [2], "quote": "..."}]}

- "sentences": your answer, one sentence per item. "passages": the numbers of the passages that sentence rests on. "quote": a short phrase (a few words) copied exactly from one of those passages, to show where.
- Every sentence needs at least one passage and a quote. A sentence without them will be deleted.
- If the passages do not contain enough to answer, reply {"abstain": true, "missing": "<one sentence on what is missing>", "sentences": []}."""


class LocalAnswerError(RuntimeError):
    """The local model could not be reached or gave an unusable reply. Nothing was billed."""


def is_local(model: str) -> bool:
    return model.startswith(PREFIX)


def _words(text: str) -> str:
    """Lower-case words separated by single spaces, padded, so `in` matches whole words only."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", fold(text).lower())) + " "


def _content_words(words: str) -> int:
    return sum(w not in STOPWORDS for w in words.split())


def _neutralise(text: str) -> str:
    """Passage text can't contain the prompt's passage delimiters."""
    return re.sub(r"<(/?)\s*passage", r"‹\1passage", text, flags=re.IGNORECASE)


def _prompt(question: str, hits, kg_summary: str) -> str:
    parts = []
    for n, h in enumerate(hits, 1):
        p = h.passage
        parts.append(f"<passage {n}>\nwork={p.work}; kind={p.kind}; translator={p.translator}; ref={p.ref}\n"
                     f"{_neutralise(p.text)}\n</passage {n}>")
    if kg_summary:
        parts.append(f"Knowledge-graph summary (co-occurrence statistics, not a source):\n{kg_summary}")
    parts.append(f"Question: {question}\n\n{INSTRUCTIONS}")
    return "\n\n".join(parts)


def call(model: str, system: str, user: str) -> tuple[dict, int, int]:
    """The parsed JSON reply, and the prompt and output token counts."""
    name = model[len(PREFIX):]
    try:
        r = requests.post(f"{config.OLLAMA_HOST}/api/chat", timeout=config.LOCAL_ANSWER_TIMEOUT, json={
            "model": name, "stream": False, "think": False, "format": ANSWER_SCHEMA,
            "options": {"temperature": 0, "num_ctx": config.LOCAL_ANSWER_CONTEXT},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        r.raise_for_status()
        body = r.json()
        data = json.loads(body["message"]["content"])
        tokens = int(body.get("prompt_eval_count") or 0), int(body.get("eval_count") or 0)
    except (requests.RequestException, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise LocalAnswerError(f"local model {name} at {config.OLLAMA_HOST}: {exc}") from None
    if not isinstance(data, dict):
        raise LocalAnswerError(f"local model {name} gave an unusable reply")
    return data, *tokens


def answer_locally(answer, hits, kg_summary: str, system_prompt: str, not_in_sources: str):
    """Fill `answer` (a qa.Answer) from the local model. Mutates and returns it."""
    from ikg.qa import Citation

    data, answer.input_tokens, answer.output_tokens = call(answer.model, system_prompt,
                                                           _prompt(answer.question, hits, kg_summary))
    answer.cost_usd = 0.0  # a local model: no API charge
    answer.model_abstained = data.get("abstain") is True
    texts = {n: _words(h.passage.text) for n, h in enumerate(hits, 1)}
    kept, numbering, dropped = [], {}, 0
    sentences = data.get("sentences")
    for s in sentences if isinstance(sentences, list) else []:
        if not isinstance(s, dict):
            dropped += 1
            continue
        # The model's own markers or line breaks would look like verified citations: remove them.
        text = re.sub(r"\s+", " ", re.sub(r"\[\d+\]", "", str(s.get("text", "")))).strip()
        listed = s.get("passages") if isinstance(s.get("passages"), list) else []
        quote = _words(str(s.get("quote", "")))
        found = []
        if _content_words(quote) >= MIN_QUOTE_WORDS:
            found = [n for n in dict.fromkeys(listed) if isinstance(n, int) and n in texts and quote in texts[n]]
        if not text or not found:
            dropped += 1
            continue
        markers = []
        for n in found:  # only passages that contain the quote are cited
            p = hits[n - 1].passage
            if p.id not in numbering:
                numbering[p.id] = len(numbering) + 1
                answer.citations.append(Citation(p.id, p.ref, str(s.get("quote", "")).strip()))
            markers.append(numbering[p.id])
        kept.append(text + "".join(f"[{m}]" for m in sorted(set(markers))))
    answer.dropped_sentences = dropped
    if not kept:
        answer.abstained = True
        missing = str(data.get("missing") or "").strip()
        if not missing and dropped:
            missing = f"The model's {dropped} sentence(s) did not cite the passages verifiably, so none are shown."
        answer.text = f"{not_in_sources}\n{missing or 'The passages do not answer the question.'}"
        answer.citations = []
        return answer
    body = " ".join(kept)
    if dropped:
        body += f"\n\n[{dropped} sentence(s) without a verifiable citation were removed.]"
    refs = "\n".join(f"[{numbering[c.passage_id]}] {c.ref} - {c.passage_id}" for c in answer.citations)
    answer.text = f"{body}\n\nSources:\n{refs}"
    return answer
