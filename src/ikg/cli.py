"""Command-line interface: ikg fetch | build | stats | search | ask | analyze | eval | sparql | serve."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Settings in .env (API keys, tokens), found from the current directory upwards like data/, must be
# loaded before ikg.config reads the environment. Variables already set in the shell take precedence.
load_dotenv(find_dotenv(usecwd=True))

from ikg import config  # noqa: E402
from ikg.pipeline import BuildError, Runtime, build, passages_from_disk  # noqa: E402
from ikg.retrieve import search  # noqa: E402
from ikg.sources import fetch, load_works  # noqa: E402


def _select_works(names: list[str] | None):
    try:
        works = load_works()
    except ValueError as exc:
        sys.exit(f"Registry error: {exc}")
    if names:
        works = [w for w in works if w.id in names]
    return works


def _runtime() -> Runtime:
    try:
        return Runtime.load()
    except FileNotFoundError as exc:
        sys.exit(str(exc))
    except ValueError as exc:  # artefacts that don't match the manifest, or an invalid snapshot
        sys.exit(f"Cannot load the built corpus: {exc}")


def cmd_fetch(args):
    try:
        written = fetch(_select_works(args.works), force=args.force)
    except (ValueError, OSError) as exc:
        sys.exit(f"Fetch failed: {exc}")
    print(f"Downloaded {len(written)} file(s) to {config.RAW_DIR}" if written else "All files already present.")


def cmd_build(args):
    try:
        build(_select_works(args.works), with_passages=args.with_passages, embed=args.embed)
    except BuildError as exc:
        sys.exit(f"Build failed: {exc}")


def _verified():
    """Exit with a clear message unless the build on disk is complete and matches its manifest."""
    try:
        Runtime.verify()
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))


def cmd_stats(args):
    _verified()
    passages = passages_from_disk()
    by_work = Counter(p.work for p in passages)
    kinds = Counter((p.work, p.kind) for p in passages)
    for w, n in sorted(by_work.items()):
        detail = ", ".join(f"{k}={v}" for (pw, k), v in sorted(kinds.items()) if pw == w)
        print(f"{w:<20} {n:>6} passages  ({detail})")


def _retrieval(rt, mode):
    """retrieve.search's mode and dense search; exits with a message if they can't be used."""
    retrieval = rt.retrieval(mode)
    if retrieval["mode"] != "bm25" and retrieval.get("dense") is None:
        sys.exit(f"{retrieval['mode']} retrieval needs embeddings: rebuild with `ikg build --embed`")
    if retrieval["mode"] != "bm25" and not rt.prepare_dense():  # fail here, clearly, not on the first question
        sys.exit(f"{retrieval['mode']} retrieval is unavailable: {rt.dense_error}")
    return retrieval


def cmd_search(args):
    rt = _runtime()
    for h in search(rt.index, rt.gazetteer, args.question, k=args.k, works=args.works,
                    primary_only=args.primary_only, passage_entities=rt.passage_entities,
                    **_retrieval(rt, args.mode)):
        snippet = " ".join(h.passage.text.split())[:240]
        print(f"{h.score:7.3f}  {h.passage.id}  {h.passage.ref}\n        {snippet}...\n")


def cmd_ask(args):
    from ikg.qa import ask
    rt = _runtime()
    ans = ask(args.question, rt.index, rt.gazetteer, graph=rt.graph, passage_entities=rt.passage_entities,
              k=args.k, works=args.works, primary_only=args.primary_only, model=args.model, effort=args.effort,
              retrieval=_retrieval(rt, args.mode))
    print("The model declined to answer this question." if ans.refused else ans.text)
    cost = f", about ${ans.cost_usd:.4f}" if ans.cost_usd is not None else ""
    print(f"\n({ans.model}, effort {ans.effort}: {ans.input_tokens:,} input + {ans.output_tokens:,} output tokens{cost})")
    if args.show_retrieval:
        print("\nRetrieved:")
        for h in ans.hits:
            print(f"  {h.score:6.2f}  {h.passage.id}")


def cmd_analyze(args):
    from ikg.analysis import write_report
    from ikg.entities import read_mentions
    rt = _runtime()  # checks the build; its gazetteer is the one the mentions came from
    path = write_report(passages_from_disk(), read_mentions(config.MENTIONS_FILE), rt.gazetteer,
                        config.REPORTS_DIR)
    print(f"Wrote {path}")


def cmd_fidelity(args):
    from ikg import fidelity
    if args.check or args.llm or args.no_expand:
        sys.exit("--fidelity can't be combined with --check, --llm or --no-expand.")
    if config.JUDGE_EFFORT not in config.EFFORTS:
        sys.exit(f"IKG_JUDGE_EFFORT must be one of {', '.join(config.EFFORTS)}, not {config.JUDGE_EFFORT!r}.")
    try:
        items = fidelity.load_items(config.FIDELITY_FILE)
    except (OSError, ValueError) as exc:
        sys.exit(f"Fidelity items: {exc}")
    if not any(i.status == "approved" for i in items) and not args.include_drafts:
        sys.exit("No fidelity item is approved by a subject expert yet (eval/fidelity/README.md). "
                 "Use --include-drafts to try the drafts; those results are not reportable.")
    result = fidelity.run(items, runtime=None if args.baseline else _runtime(), baseline=args.baseline,
                          include_drafts=args.include_drafts, judge_model=args.judge_model, model=args.model,
                          effort=args.effort, k=args.k, out_dir=config.EVAL_RESULTS_DIR)
    for row in result["rows"]:
        failed = [c["check"] for c in row["checks"] if not c["passed"]]
        print(f"{'PASS' if row['passed'] else 'FAIL'}  {row['id']} ({row['property']})"
              + (f": {'; '.join(failed)}" if failed else ""))
    print(json.dumps(result["summary"], indent=2))


def cmd_eval(args):
    if args.fidelity:
        return cmd_fidelity(args)
    from ikg.evaluate import load_items, regressions, run
    baseline = None
    if args.check:
        if not args.check.exists():
            sys.exit(f"Baseline file not found: {args.check}")
        baseline = json.loads(args.check.read_text(encoding="utf-8"))
        if baseline.get("k", args.k) != args.k:
            sys.exit(f"Baseline was recorded with k={baseline['k']}; run with -k {baseline['k']}.")
    rt = _runtime()
    items = load_items(config.EVAL_FILE)
    if args.retriever == "all":
        if args.llm or baseline is not None:
            sys.exit("--retriever all compares retrieval only; run --llm or --check with one retriever")
        return _compare_retrievers(rt, items, args)
    retrieval = _retrieval(rt, args.retriever)
    if baseline is not None and baseline.get("retriever", "bm25") != retrieval["mode"]:
        sys.exit(f"Baseline was recorded with the {baseline.get('retriever', 'bm25')} retriever, this run uses "
                 f"{retrieval['mode']}. Pass --retriever, or build with `ikg build --embed` for hybrid.")
    result = run(items, rt.index, rt.gazetteer, rt.passage_entities, k=args.k,
                 llm=args.llm, graph=rt.graph, out_dir=config.EVAL_RESULTS_DIR, expand=not args.no_expand,
                 model=args.model, effort=args.effort, retrieval=retrieval)
    for row in result["rows"]:
        if row["type"] == "retrieval" and not row["hit_at_k"]:
            print(f"MISS  {row['id']}: top={row['retrieved'][:3]}")
    print(json.dumps(result["summary"], indent=2))
    if baseline is not None:
        worse = regressions(result["summary"], baseline)
        if worse:
            sys.exit("Regression against " + str(args.check) + ":\n  " + "\n  ".join(worse))
        print(f"No regression against {args.check}.")


def _compare_retrievers(rt, items, args):
    """One table: BM25, dense and hybrid on every question type."""
    from ikg.evaluate import run
    summaries = {}
    for mode in ("bm25", "dense", "hybrid"):
        summaries[mode] = run(items, rt.index, rt.gazetteer, rt.passage_entities, k=args.k,
                              expand=not args.no_expand, retrieval=_retrieval(rt, mode),
                              out_dir=config.EVAL_RESULTS_DIR)["summary"]
    types = sorted(summaries["bm25"]["retrieval_by_type"])
    print(f"{'retriever':<10} {'hit@1':>6} {'hit@' + str(args.k):>6} {'MRR':>6}   " + " ".join(f"{t:>5}" for t in types))
    for mode, s in summaries.items():
        by = s["retrieval_by_type"]
        print(f"{mode:<10} {s['retrieval_hit_at_1']:>6.3f} {s['retrieval_hit_at_k']:>6.3f} {s['retrieval_mrr']:>6.3f}   "
              + " ".join(f"{by[t]['hit_at_k']:>5.2f}" for t in types))
    print(f"\nPer type: hit@{args.k}. Items: {summaries['bm25']['n_retrieval']} retrieval. "
          f"Each run is saved in {config.EVAL_RESULTS_DIR}.")


def cmd_vectors(args):
    from ikg.segment import PRIMARY_KINDS
    from ikg.vectorstore import VectorStoreError, compare
    rt = _runtime()
    try:
        exact = rt.vector_store("file")
        other = rt.vector_store(args.store, log=lambda m: print(m, file=sys.stderr))
        report = compare(exact, other, rt.embeddings, rt.index.passages, PRIMARY_KINDS, n=args.n, k=args.k)
    except VectorStoreError as exc:
        sys.exit(str(exc))
    print(json.dumps(report, indent=2))


def cmd_sparql(args):
    from rdflib import Graph
    _verified()
    g = Graph()
    g.parse(config.RDF_FILE, format="turtle")
    query = args.query
    if not query.lstrip().upper().startswith("PREFIX"):
        query = ("PREFIX ikg: <https://example.org/ikg#>\n"
                 "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n" + query)
    for row in g.query(query):
        print("\t".join(str(v) for v in row))


def cmd_curator(args):
    from ikg import curators
    try:
        if args.action == "add":
            token = curators.add_curator(args.name)
            print(f"Curator {args.name!r} added. Their login token (shown once, not stored):\n\n  {token}\n")
        elif args.action == "remove":
            curators.remove_curator(args.name)
            print(f"Curator {args.name!r} removed; their token no longer logs in.")
        else:
            for c in curators.load_curators():
                print(f"{c.name:<24} added {c.added}")
    except ValueError as exc:
        sys.exit(str(exc))


def cmd_provenance(args):
    from ikg import curators
    for e in curators.recent(args.n)[::-1]:
        print(f"{e['time']}  {e['curator']:<16} {e['action']:<18} {e['target']}  {e['detail']}".rstrip())


def cmd_serve(args):
    try:
        import uvicorn
    except ImportError:
        sys.exit('The web app needs extra packages: pip install -e ".[web]"')
    from ikg.web.app import create_app
    uvicorn.run(create_app(), host=args.host, port=args.port)


def _model_args(p):
    p.add_argument("--model", help=f"answering model (default {config.MODEL}; env IKG_ANSWER_MODEL)")
    p.add_argument("--effort", choices=config.EFFORTS,
                   help=f"answering effort (default {config.EFFORT}; env IKG_ANSWER_EFFORT)")


def main(argv=None):
    # Translations use IAST diacritics; Windows consoles default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="ikg", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="download source texts from Project Gutenberg")
    p.add_argument("--works", nargs="*")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("build", help="segment, extract entities, index, build graph + RDF")
    p.add_argument("--works", nargs="*")
    p.add_argument("--with-passages", action="store_true", help="include every passage in the RDF export")
    p.add_argument("--embed", action=argparse.BooleanOptionalAction, default=None,
                   help="write passage embeddings with IKG_EMBED_MODEL (default: IKG_EMBED, off); "
                        'needs pip install -e ".[embed]"')
    p.set_defaults(func=cmd_build)

    sub.add_parser("stats", help="corpus statistics").set_defaults(func=cmd_stats)

    for name, func, help_ in (("search", cmd_search, "retrieve passages (no API call)"),
                              ("ask", cmd_ask, "answer a question with citations (calls Claude)")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("question")
        p.add_argument("-k", type=int, default=8)
        p.add_argument("--works", nargs="*")
        p.add_argument("--primary-only", action="store_true", help="exclude commentary, introductions, scholarship")
        p.add_argument("--mode", choices=["bm25", "dense", "hybrid"],
                       help=f"retrieval mode (default {config.RETRIEVER}; env IKG_RETRIEVER; "
                            "bm25 if the build has no embeddings)")
        if name == "ask":
            p.add_argument("--show-retrieval", action="store_true")
            _model_args(p)
        p.set_defaults(func=func)

    sub.add_parser("analyze", help="write reports/corpus-analysis.md").set_defaults(func=cmd_analyze)

    p = sub.add_parser("eval", help="run the evaluation set")
    p.add_argument("-k", type=int, default=8)
    p.add_argument("--llm", action="store_true", help="also evaluate answers and citations (calls Claude)")
    p.add_argument("--no-expand", action="store_true", help="ablation: disable knowledge-graph query expansion")
    p.add_argument("--retriever", choices=["bm25", "dense", "hybrid", "all"],
                   help=f"retrieval mode (default {config.RETRIEVER}); 'all' prints BM25, dense and hybrid side by side")
    _model_args(p)
    p.add_argument("--fidelity", action="store_true",
                   help="run the SOT-fidelity benchmark instead (calls Claude to answer and to judge)")
    p.add_argument("--baseline", action="store_true",
                   help="with --fidelity: answer with the plain model and no retrieval, for comparison")
    p.add_argument("--include-drafts", action="store_true",
                   help="with --fidelity: also score items no expert has approved yet (not reportable)")
    p.add_argument("--judge-model", help=f"with --fidelity: judge model (default {config.JUDGE_MODEL}; env IKG_JUDGE_MODEL)")
    p.add_argument("--check", type=Path, metavar="BASELINE",
                   help="exit non-zero if any metric in this baseline JSON got worse (CI gate)")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("vectors", help="compare a vector store with exact search: recall@k and latency")
    p.add_argument("action", choices=["compare"])
    p.add_argument("--store", default=None,
                   help="the store to measure: a postgresql:// URL, or 'file' (default: IKG_VECTOR_STORE)")
    p.add_argument("-n", type=int, default=200, help="sampled queries (default 200)")
    p.add_argument("-k", type=int, default=8)
    p.set_defaults(func=cmd_vectors)

    p = sub.add_parser("sparql", help="run a SPARQL query over the RDF export")
    p.add_argument("query")
    p.set_defaults(func=cmd_sparql)

    p = sub.add_parser("curator", help="manage named curators who can log in to the admin")
    p.add_argument("action", choices=["add", "remove", "list"])
    p.add_argument("name", nargs="?", default="")
    p.set_defaults(func=cmd_curator)

    p = sub.add_parser("provenance", help="show the provenance log (who did what, when)")
    p.add_argument("-n", type=int, default=50, help="number of entries (default 50)")
    p.set_defaults(func=cmd_provenance)

    p = sub.add_parser("serve", help="run the web viewer and admin")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
