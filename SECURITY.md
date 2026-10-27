# Security Policy

DeepMap is a [Deep Fusion Labs](https://github.com/deepfusionlabs) open-source project. We take security
seriously and appreciate coordinated, good-faith disclosure.

## Reporting a vulnerability

**Please do NOT open a public issue for a security vulnerability.** Instead, report it privately through
GitHub's private vulnerability reporting:

> Go to the repository's **Security** tab → **Report a vulnerability** (GitHub Security Advisories).

This opens a private advisory visible only to you and the maintainers. If you cannot use GitHub advisories,
open a regular issue that says only *"security report: please open a private channel"* (no details) and a
maintainer will follow up.

Please include, as far as you can:

- a description of the issue and its impact (what an attacker can do),
- the affected component and version / commit,
- reproduction steps or a proof-of-concept,
- any suggested remediation.

## What to expect

- **Acknowledgement:** we aim to acknowledge a report within **5 business days** (best-effort; DeepMap is a
  small project).
- **Assessment & fix:** we triage severity, develop a fix on a private branch, and coordinate a release.
- **Disclosure:** we prefer **coordinated disclosure**: please give us a reasonable window to ship a fix
  before publishing details. We will credit you in the advisory unless you ask us not to.

## Scope

**In scope:** the DeepMap plugin code in this repository: the sync/index/embed pipeline, the retriever, the
streamable-HTTP MCP servers, the read-only Cypher validator behind `deepmap_cypher` (it must refuse writes,
`LOAD CSV` and procedures outside its read-only allowlist), the CLI and the one-command provisioner (`build/`), plus
the hooks (`hooks/`), the installers and the `/deep-map` commands.

**Out of scope** (report upstream instead):

- **Neo4j** itself: DeepMap *fetches, does not bundle* Neo4j Community Edition and talks to it over Bolt.
  Report Neo4j vulnerabilities to [Neo4j](https://neo4j.com/).
- The **upstream AST engine** (`graphify`/`graphifyy`) and the **embedding model weights**: report to their
  respective projects.
- A user's own **exposed / misconfigured Neo4j instance** (see the hardening note below): that is a
  deployment concern, not a DeepMap defect.

## Security model & hardening notes (please read before reporting)

DeepMap is designed to run **locally, on a developer's machine**:

- The MCP servers run **detached on `127.0.0.1`**, on a port pair of their own per project (`8787` search and
  impact, `8788` embed for the first project): loopback only, not exposed to the network by default. Each
  project's Neo4j binds its Bolt and HTTP ports to `127.0.0.1` only.
- The provisioner sets a **local dev password (`deepmap`)** and relaxes Neo4j's minimum-password-length for
  that dev convenience. This is intentional for a **local, single-developer** graph and is **NOT** a
  recommendation for a shared, remote, or internet-exposed Neo4j instance: set a strong password and bind
  appropriately if you deviate from the local-only model.
- By default DeepMap indexes **your** source into a **local** graph and sends nothing to any external
  service. Two features are explicitly **opt-in and off by default**: (1) **summary enrichment**, which
  uses your **existing Claude session** (`claude -p`) to write intent summaries for undocumented code,
  with no new service or API key, sending undocumented source spans nowhere you aren't already sending code
  via Claude Code, and only when you enable it (`DEEPMAP_ENRICH=1` + `--confirm-spend`); and (2) the
  `benchmark/` harness, which can call external model APIs **only** when you configure a key and confirm
  the spend. Neither is invoked by normal product use.

A finding that depends on a user overriding these defaults (exposing the loopback ports, keeping the
dev password on a shared instance) is a **deployment hardening** matter, but we still welcome the report so
we can improve the docs and defaults.

## Supported versions

DeepMap is an early release, versioned `0.x`. Security fixes are applied to the **latest released `0.x`**
version; there is no long-term support for older `0.x` releases yet. Once DeepMap reaches `1.0`, this policy
will be updated with a concrete support window.

| Version | Supported |
|---|---|
| latest `0.x` | ✅ |
| older `0.x`  | ❌ (upgrade to the latest) |

## Safe harbor

We consider good-faith security research that respects this policy (no privacy violations, no service
disruption, no data destruction, and only against your own local installation) to be authorized. We will
not pursue or support legal action against researchers who follow it. If in doubt, ask first via a private
advisory.
