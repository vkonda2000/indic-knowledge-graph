"""Read-only SPARQL over the RDF export, for clients that shouldn't be trusted with the graph.

`run(query)` accepts SELECT and ASK only (an update such as INSERT, DELETE or LOAD doesn't parse as a
query), refuses SERVICE (a federated call to another endpoint), and runs the query in a child process
that is stopped after `timeout` seconds, returning at most `max_rows` rows. Used by the MCP server
(docs/08 B1). Run as `python -m ikg.sparql RDF_FILE MAX_ROWS` it is that child: query on stdin, JSON out.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

MAX_ROWS = 500
TIMEOUT = float(os.environ.get("IKG_SPARQL_TIMEOUT", "10"))  # seconds, including loading the graph
PREFIXES = ("PREFIX ikg: <https://example.org/ikg#>\nPREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
            "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n")


class SparqlError(ValueError):
    pass


def guard(query: str) -> str:
    """The query, with the default prefixes if it declares none; SparqlError unless it is a safe SELECT/ASK."""
    from rdflib.plugins.sparql import prepareQuery
    if not query.lstrip().upper().startswith(("PREFIX", "BASE")):
        query = PREFIXES + query
    try:
        prepared = prepareQuery(query)
    except Exception as exc:  # syntax errors, and every update form
        raise SparqlError(f"not a SPARQL SELECT or ASK query: {exc}") from None
    if prepared.algebra.name not in ("SelectQuery", "AskQuery"):
        raise SparqlError(f"only SELECT and ASK are allowed, not {prepared.algebra.name}")
    if "ServiceGraphPattern" in repr(prepared.algebra):
        raise SparqlError("SERVICE (a federated query to another endpoint) is not allowed")
    return query


def run(query: str, rdf_file: Path, timeout: float = TIMEOUT, max_rows: int = MAX_ROWS) -> dict:
    """{columns, rows, boolean, truncated} for a guarded query, run in a child process."""
    query = guard(query)
    try:
        done = subprocess.run([sys.executable, "-m", "ikg.sparql", str(rdf_file), str(max_rows)], input=query,
                              capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SparqlError(f"the query took longer than {timeout:g} s and was stopped") from None
    if done.returncode != 0:
        last = done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "unknown error"
        kind = last.split(":", 1)[0] if ":" in last else "error"  # the exception's name, not paths or hosts
        raise SparqlError(f"the query failed while running ({kind})")
    return json.loads(done.stdout)


def _child(rdf_file: str, max_rows: int) -> None:
    from rdflib import Graph
    query = sys.stdin.buffer.read().decode("utf-8")  # not the console's code page (cp1252 on Windows)
    g = Graph()
    g.parse(rdf_file, format="turtle")
    result = g.query(query)
    if result.type == "ASK":
        print(json.dumps({"columns": [], "rows": [], "boolean": bool(result.askAnswer), "truncated": False}))
        return
    columns, rows, truncated = [str(v) for v in result.vars], [], False
    for row in result:
        if len(rows) == max_rows:
            truncated = True
            break
        rows.append([None if v is None else str(v) for v in row])
    print(json.dumps({"columns": columns, "rows": rows, "boolean": None, "truncated": truncated}))


if __name__ == "__main__":
    _child(sys.argv[1], int(sys.argv[2]))
