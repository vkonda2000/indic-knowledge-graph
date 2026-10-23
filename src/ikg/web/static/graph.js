// Knowledge-graph explorer: loads /api/graph, renders with Cytoscape, shows entity details on click.
(function () {
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  const typeColor = {
    Person: color("--c-person"), Deity: color("--c-deity"), Place: color("--c-place"),
    Concept: color("--c-concept"), SchoolOfThought: color("--c-school"),
  };
  const panel = document.getElementById("panel");
  let cy;

  function el(tag, attrs = {}, text) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function params() {
    const types = [...document.querySelectorAll(".type:checked")].map((c) => c.value);
    return new URLSearchParams({
      work: document.getElementById("work").value,
      min_weight: document.getElementById("min_weight").value || "1",
      max_nodes: document.getElementById("max_nodes").value || "60",
      types: types.join(","),
    });
  }

  async function load() {
    const res = await fetch("/api/graph?" + params());
    if (!res.ok) { panel.replaceChildren(el("p", { class: "notice error" }, "Could not load the graph.")); return; }
    const data = await res.json();
    const maxMentions = Math.max(1, ...data.nodes.map((n) => n.data.mentions));
    const maxWeight = Math.max(1, ...data.edges.map((e) => e.data.weight));
    if (cy) cy.destroy();
    cy = cytoscape({
      container: document.getElementById("cy"),
      elements: [...data.nodes, ...data.edges],
      style: [
        { selector: "node", style: {
            "label": "data(label)", "font-size": 11, "color": color("--text"),
            "text-outline-color": color("--bg"), "text-outline-width": 2,
            "background-color": (n) => typeColor[n.data("type")] || color("--muted"),
            "width": (n) => 10 + 26 * Math.sqrt(n.data("mentions") / maxMentions),
            "height": (n) => 10 + 26 * Math.sqrt(n.data("mentions") / maxMentions) } },
        { selector: "edge", style: {
            "width": (e) => 0.5 + 6 * (e.data("weight") / maxWeight),
            "line-color": color("--edge"), "opacity": 0.7, "curve-style": "haystack" } },
        { selector: "node:selected", style: { "border-width": 3, "border-color": color("--accent") } },
        { selector: ".faded", style: { "opacity": 0.12 } },
      ],
      layout: { name: "cose", animate: false, randomize: true, nodeRepulsion: () => 400000,
                idealEdgeLength: () => 120, edgeElasticity: () => 50, gravity: 0.2, nodeOverlap: 20,
                numIter: 2000, padding: 30 },
    });
    cy.on("tap", "node", (evt) => select(evt.target));
    cy.on("tap", (evt) => { if (evt.target === cy) cy.elements().removeClass("faded"); });
    if (!data.nodes.length) panel.replaceChildren(el("p", { class: "muted" }, "No entities match these filters."));
  }

  async function select(node) {
    const hood = node.closedNeighborhood();
    cy.elements().addClass("faded");
    hood.removeClass("faded");
    const res = await fetch("/api/entity/" + encodeURIComponent(node.id()));
    if (!res.ok) return;
    const e = await res.json();
    const parts = [];
    const h = el("h2"); h.append(el("a", { href: "/entity/" + encodeURIComponent(e.id) }, e.label)); parts.push(h);
    parts.push(el("p", { class: "muted" }, `${e.type}${e.iast ? " · " + e.iast : ""} · ${e.mentions} mentions`));
    if (e.renderings.length) parts.push(el("p", { class: "small" }, "Translators' renderings: " + e.renderings.join(", ")));
    parts.push(el("h3", {}, "Strongest connections"));
    const ul = el("ul", { class: "plain" });
    for (const n of e.neighbours) {
      const li = el("li"); li.append(el("a", { href: "/entity/" + encodeURIComponent(n.id) }, n.label), " ", el("span", { class: "muted" }, `${n.weight}`));
      ul.append(li);
    }
    parts.push(ul, el("h3", {}, "Passages"));
    for (const p of e.passages) {
      const div = el("div", { class: "mini-hit" });
      div.append(el("a", { href: "/passage/" + encodeURIComponent(p.id) }, p.ref), el("p", { class: "small" }, p.snippet));
      parts.push(div);
    }
    panel.replaceChildren(...parts);
  }

  document.getElementById("apply").addEventListener("click", load);
  load();
})();
