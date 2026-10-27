# DeepMap — Third-Party Licenses

DeepMap's own code is **MIT** (see `LICENSE`). This file enumerates every
third-party component DeepMap depends on, its license, how it is distributed, and
the resulting obligation. It is the human-readable companion to the machine-readable
SBOM (`sbom.cdx.json`, CycloneDX) and to `NOTICE`.

> **Pinned versions.** The three dependencies with known breaking releases are pinned to the ranges
> they were validated on — the same ranges as `requirements.txt`, both installers, CI and the SBOM,
> each installed with `pip install --upgrade` so an older copy is lifted into the range:
> `neo4j>=6.0.1,<7` (validated at 6.0.1, 6.2.0 and 6.3.1), `mcp>=1.24,<2` (validated at 1.24.0,
> 1.28.1 and 1.30.0) and `graphifyy>=0.8.39,<0.8.52` (validated at 0.8.39 and 0.8.51).
> `tests/compliance_gates.py` fails if this file, `NOTICE` or `sbom.cdx.json` drifts from
> `requirements.txt`. The other dependencies stay unpinned (no known breaking release), except `anyio`,
> declared `anyio>=4.5` — mcp's own floor — because DeepMap imports it directly; license and distribution
> do not change with the patch version.

## Distribution legend
- **bundled (pip)** — installed from PyPI into the Python environment as a dependency; DeepMap does not redistribute it.
- **fetched** — downloaded by the installer at setup time from the official source; **not** redistributed by DeepMap.
- **prerequisite** — a separate program DeepMap talks to over a network protocol; not linked, not bundled.

## Components

| Component | License | Distribution | Obligation | Notes |
|---|---|---|---|---|
| **DeepMap** (own code) | MIT | — | — | The product. |
| **Neo4j Community Edition** 5.x (the provisioner fetches 5.26.32 by default; a store created with an earlier 5.26 release keeps it until setup moves it to 5.26.32) | **GPLv3** (source headers: version 3 or any later version) | **prerequisite (fetched from dist.neo4j.org, not bundled)** | Do not redistribute/relicense; keep arms-length over Bolt | The only copyleft component. Fetch-not-bundle + arms-length Bolt keeps DeepMap's MIT code free of the GPLv3 combined-work obligation. Its code is not modified: setup writes only DeepMap's own settings into `neo4j.conf` on the user's machine (including `dbms.usage_report.enabled=false`). |
| Upstream multi-language extraction engine — `graphify` / `graphifyy` (Graphify-Labs/graphify, formerly safishamsi/graphify) | MIT (every release the pin allows; upstream is Apache-2.0 from 0.9.25 — re-check before widening the pin) | **bundled (pip, bare)** | Retain MIT notice + copyright | Used **unchanged** as the multi-language extractor (`DEEPMAP_EXTRACTOR=graphify\|auto`). Installed BARE by both installers + `requirements.txt` (`pip install --upgrade "graphifyy>=0.8.39,<0.8.52"`, **never** `graphifyy[all]`; validated at 0.8.39 and 0.8.51 — 0.8.52+ and 0.9.x are outside the validated range), so no optional LLM-provider extras (openai/anthropic/bedrock) come along — a default install ships **no LLM SDK**. DeepMap invokes only `graphify update --no-cluster` (NO LLM, NO clustering, NO network) → zero-egress by construction; base wheel pulls ~25 tree-sitter grammars. The PyPI package is `graphifyy`; its import name, `graphify`, is used unchanged. |
| sentence-transformers | Apache-2.0 | bundled (pip) | NOTICE + retain NOTICE/patent-grant | Embedding pipeline. |
| transformers | Apache-2.0 | bundled (pip) | NOTICE | Model loading. |
| torch (CPU) | BSD-3-Clause | bundled (pip) | Retain copyright + disclaimer | CPU tensor backend. |
| neo4j (Python Bolt driver) | Apache-2.0 AND Python-2.0 (PyPI license expression) | bundled (pip) | NOTICE | Bolt client. Pinned `neo4j>=6.0.1,<7`. |
| mcp (MCP Python SDK) | MIT | bundled (pip) | NOTICE | Both MCP servers `from mcp.server.fastmcp import FastMCP` at module load. Pinned `mcp>=1.24,<2`. |
| anyio | MIT | bundled (pip; installed next to mcp) | NOTICE | Imported directly: the MCP servers run each tool body in a worker thread under a deadline (`build/mcp_bounds.py`) and bound the search server's project-guard roots request (`build/deepmap_search_server.py`). Declared `anyio>=4.5` in `requirements.txt` — mcp's own floor, so it never changes what pip resolves; no ceiling (no known breaking release). Both installers install it next to mcp with the same floor; CI gets it through mcp. |
| huggingface_hub | Apache-2.0 | bundled (pip) | NOTICE | Pre-downloads the embedding model (`snapshot_download`). |
| PyYAML | MIT | bundled (pip) | NOTICE | Parses the benchmark `repos.yaml` / `questions.yaml`; both installers `pip install pyyaml`. The harness has a stdlib fallback, but the dep is shipped, so it is attributed. |
| `nomic-ai/modernbert-embed-base` (model weights) | Apache-2.0 | fetched (about 0.6 GB: `/deep-map:setup` and both installers skip the ONNX / OpenVINO exports) | NOTICE | **General-text** model (768-dim), not code-trained. |

> **ranx (MIT) — evaluated, NOT shipped.** Not a dependency: fusion is the pure-Python `_weighted_rrf`
> (`build/retrieval.py`), nothing imports ranx, and `requirements.txt` and the install scripts exclude it,
> so it carries no attribution obligation here.

> **mcp-neo4j-cypher (neo4j-contrib/mcp-neo4j, MIT) — NOT shipped.** Not a dependency: DeepMap's read-only
> Cypher tool (`deepmap_cypher`) is its own code. Neo4j's official MCP server (`neo4j/mcp`) is GPL-3.0 and is
> likewise not shipped.

> **APOC (Core) (Apache-2.0) — NOT used.** DeepMap neither fetches, enables nor calls APOC. The Neo4j
> distribution carries a disabled `labs/apoc-*-core.jar`; DeepMap leaves it disabled.

## License compatibility summary
- DeepMap (MIT) + permissive deps (MIT / Apache-2.0 / BSD-3): clean. Apache-2.0 → MIT is one-way-clean; no GPLv2 in the tree, so the Apache/GPL patent-clause conflict does not arise.
- The single copyleft component (Neo4j Community, GPLv3) is a fetched-not-bundled prerequisite accessed over Bolt — no combined-work obligation under the standard reading.
- **No AGPL** anywhere. DeepMap is **local-only** (not SaaS), so no network-copyleft / SaaS-disclosure question arises.
- Benchmark repositories are cloned for measurement under each repo's own license and recorded — **never redistributed**.

*Not legal advice.*
