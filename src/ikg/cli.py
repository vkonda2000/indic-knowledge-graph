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
        build(_select_works(args.works), with_passages=args.with_passages)
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


def cmd_search(args):
    rt = _runtime()
    for h in search(rt.index, rt.gazetteer, args.question, k=args.k, works=args.works,
                    primary_only=args.primary_only, passage_entities=rt.passage_entities):
        snippet = " ".join(h.passage.text.split())[:240]
        print(f"{h.score:6.2f}  {h.passage.id}  {h.passage.ref}\n        {snippet}...\n")


def cmd_ask(args):
    from ikg.qa import ask
    rt = _runtime()
    ans = ask(args.question, rt.index, rt.gazetteer, graph=rt.graph, passage_entities=rt.passage_entities,
              k=args.k, works=args.works, primary_only=args.primary_only, model=args.model, effort=args.effort)
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
    result = run(load_items(config.EVAL_FILE), rt.index, rt.gazetteer, rt.passage_entities, k=args.k,
                 llm=args.llm, graph=rt.graph, out_dir=config.EVAL_RESULTS_DIR, expand=not args.no_expand,
                 model=args.model, effort=args.effort)
    for row in result["rows"]:
        if row["type"] == "retrieval" and not row["hit_at_k"]:
            print(f"MISS  {row['id']}: top={row['retrieved'][:3]}")
    print(json.dumps(result["summary"], indent=2))
    if baseline is not None:
        worse = regressions(result["summary"], baseline)
        if worse:
            sys.exit("Regression against " + str(args.check) + ":\n  " + "\n  ".join(worse))
        print(f"No regression against {args.check}.")


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
    p.set_defaults(func=cmd_build)

    sub.add_parser("stats", help="corpus statistics").set_defaults(func=cmd_stats)

    for name, func, help_ in (("search", cmd_search, "retrieve passages (no API call)"),
                              ("ask", cmd_ask, "answer a question with citations (calls Claude)")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("question")
        p.add_argument("-k", type=int, default=8)
        p.add_argument("--works", nargs="*")
        p.add_argument("--primary-only", action="store_true", help="exclude commentary, introductions, scholarship")
        if name == "ask":
            p.add_argument("--show-retrieval", action="store_true")
            _model_args(p)
        p.set_defaults(func=func)

    sub.add_parser("analyze", help="write reports/corpus-analysis.md").set_defaults(func=cmd_analyze)

    p = sub.add_parser("eval", help="run the evaluation set")
    p.add_argument("-k", type=int, default=8)
    p.add_argument("--llm", action="store_true", help="also evaluate answers and citations (calls Claude)")
    p.add_argument("--no-expand", action="store_true", help="ablation: disable knowledge-graph query expansion")
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

    p = sub.add_parser("sparql", help="run a SPARQL query over the RDF export")
    p.add_argument("query")
    p.set_defaults(func=cmd_sparql)

    p = sub.add_parser("serve", help="run the web viewer and admin")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
