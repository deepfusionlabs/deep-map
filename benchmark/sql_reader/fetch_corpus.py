#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G-SQL corpus: download the pinned public corpora of benchmark/sql_reader/PREREG.md and verify every byte.

The sources, their commits and the SHA-256 values come from the JSON block inside PREREG.md's registered block
(load_registered); this file holds no second copy of them. Everything is written under --out, which must lie outside
this repository (a corpus inside it would enter the repository's own graph and the STAGE-0 `self` corpus: design
supply minor 4). Nothing here runs DeepMap.

Layouts (the registered `layout` of a source):
  sql-set  a pinned git commit; only its .sql files under the registered `select` prefixes (every .sql file when there
           is none; the suffix compared case-insensitively),
           written from the git BLOBS (never from a checkout: a CRLF checkout changes the bytes) to <out>/<id>/tree/,
           which is then `git init`-ed with no commit, so it is a git work tree whose files are untracked and not
           ignored (the corpus "as git trees", PREREG C4). The helper clone is <out>/<id>/_git.
  clone    a pinned git commit checked out at <out>/<id>/repo (depth 1, blobs on demand, core.autocrlf false; a cone
           sparse checkout of the registered `sparse` directories when given); the project root is
           <out>/<id>/repo/<root>. A .sql file whose checked-out bytes differ from its blob is rewritten from the blob.
  raw      one file at a pinned commit, downloaded over HTTPS to <out>/<id>/tree/<path>, then `git init` as above.

The registered value of a source is the manifest of its SQL set: one line `<sha256 of the bytes>  <relative path>`
per .sql file (forward slashes, relative to the source root, sorted by the UTF-8 bytes of the path, each line ending
in LF); `sql_manifest_sha256` is the sha256 of those lines, `sql_files` their number, `sql_bytes` the total size. A
raw source registers the file's own `sha256` and `bytes` too.

fetch is idempotent: a source already present and verified is left alone; a present source that fails verification
is refused (exit 1) and never deleted: remove that exact directory by hand and fetch again.

select-ef is design section 9's choice of the one public .NET repository with committed `dotnet ef migrations script
--idempotent` output, by metadata only, before any DeepMap run (PREREG "Registered corpus"): every GitHub code-search
hit for the SQL Server idempotent guard (enumerated in file-size partitions, since one query returns at most 1,000),
its repositories ranked by stars (ties by name), the first that is not a fork, not archived, not a D3-cs corpus, holds
at least 100 .cs files and a .sql file with the guard (EF_GUARD) at its default branch head (its .sql files read, the
search's hits first, at most EF_MAX_SQL_READ). It needs `gh` signed in (GH_TOKEN / GITHUB_TOKEN are dropped from its
environment so the stored login is used) and writes <out>/select_ef_hits.json (the search, reusable with --hits) and
<out>/select_ef.json (every candidate evaluated, the selection).

usage:
  python fetch_corpus.py plan [--registered FILE]
  python fetch_corpus.py fetch --out DIR [--only ID[,ID...]] [--registered FILE] [--register]
  python fetch_corpus.py verify --out DIR [--only ID[,ID...]] [--registered FILE]
  python fetch_corpus.py manifest DIR            # the manifest of a directory's .sql files (prints; writes nothing)
  python fetch_corpus.py select-ef --out DIR [--hits DIR/select_ef_hits.json]
Exit codes: 0 done / verified; 1 a verification failed; 2 refused (nothing written); 3 a network or tool failure.
--registered FILE reads the sources from a JSON file (the registered JSON's shape) instead of PREREG.md: for
registration-time manifests and for tests/sql_prereg_gates.py; every result names the registration it used.
--register (registration time only, with --registered): the manifest fields may be null; each source is fetched and
its computed manifest printed instead of compared (the values PREREG.md then records).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PREREG = HERE / "PREREG.md"
REGISTERED_BEGIN = "<!-- G-SQL-REGISTERED BEGIN"
REGISTERED_END = "<!-- G-SQL-REGISTERED END"
PROTOCOL_BEGIN = "<!-- G-SQL-PROTOCOL BEGIN"
PROTOCOL_END = "<!-- G-SQL-PROTOCOL END"
AMENDMENTS_HEADING = "## Amendments before data"
KIT_SLOT = re.compile(r"^- \*\*Kit hash:\*\* `([^`]*)`", re.M)       # PREREG section 1 (amendment A1)
SOURCE_KEYS = {"id", "type", "layout", "url", "commit", "sql_files", "sql_bytes", "sql_manifest_sha256"}
LAYOUTS = {"sql-set", "clone", "raw"}
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
# The SQL Server form of `dotnet ef migrations script --idempotent` (EF Core 2.0 and later): every migration is
# guarded by this test of the history table (select-ef's content check).
EF_GUARD = re.compile(r"IF\s+NOT\s+EXISTS\s*\(\s*SELECT\s+\*\s+FROM\s+\[__EFMigrationsHistory\]\s+WHERE\s+"
                      r"\[MigrationId\]\s*=\s*N'")
EF_QUERY = '__EFMigrationsHistory "IF NOT EXISTS" extension:sql'
EF_EXCLUDED = ("jellyfin/jellyfin", "dotnet/eshop")      # the D3-cs corpora (PREREG_CS): never a G-SQL corpus
EF_MIN_CS_FILES = 100
EF_MAX_SQL_READ = 60                                     # .sql files read per candidate (the search's hits first)
SIZE_MAX = 393216                                        # GitHub's code search indexes files below 384 KB


def say(msg: str) -> None:
    print(f"[fetch_corpus] {msg}", flush=True)


# ---------------------------------------------------------------------------------------------------- registration
def block(text: str, begin: str, end: str) -> "tuple[str | None, str | None]":
    """(the lines strictly between the ONE line starting `begin` and the ONE line starting `end`, LF-joined plus a
    final LF; None) or (None, why). The hash-lock rule of tests/sql_prereg_gates.py (call-linking's)."""
    lines = text.replace("\r\n", "\n").split("\n")
    b = [i for i, ln in enumerate(lines) if ln.startswith(begin)]
    e = [i for i, ln in enumerate(lines) if ln.startswith(end)]
    if len(b) != 1 or len(e) != 1 or b[0] >= e[0]:
        return None, f"want exactly one {begin!r} line before exactly one {end!r} line (at {b} and {e})"
    return "\n".join(lines[b[0] + 1:e[0]]) + "\n", None


def json_in(section: str) -> dict:
    """The ONE ```json fenced block of a section, parsed. Raises ValueError."""
    found = re.findall(r"^```json\n(.*?)^```$", section, re.S | re.M)
    if len(found) != 1:
        raise ValueError(f"want exactly one ```json block, found {len(found)}")
    return json.loads(found[0])


def _prereg_part(path, begin: str, end: str) -> dict:
    p = Path(path) if path else PREREG
    text = p.read_text(encoding="utf-8")
    sec, why = block(text, begin, end)
    if sec is None:
        raise ValueError(f"{p}: {why}")
    return json_in(sec)


def load_registered(path=None) -> dict:
    """The registered JSON (sources, truth expectations, C6 queries) of PREREG.md (or of the PREREG at `path`)."""
    return _prereg_part(path, REGISTERED_BEGIN, REGISTERED_END)


def load_protocol(path=None) -> dict:
    """The protocol JSON (seeds, thresholds) of PREREG.md (or of the PREREG at `path`)."""
    return _prereg_part(path, PROTOCOL_BEGIN, PROTOCOL_END)


def amendments_block(text: str) -> "tuple[str | None, str | None]":
    """(the lines strictly between the ONE "## Amendments before data" heading line and the ONE protocol BEGIN line,
    LF-joined plus a final LF; None) or (None, why): the section tests/sql_prereg_gates.py hash-locks."""
    lines = text.replace("\r\n", "\n").split("\n")
    h = [i for i, ln in enumerate(lines) if ln == AMENDMENTS_HEADING]
    b = [i for i, ln in enumerate(lines) if ln.startswith(PROTOCOL_BEGIN)]
    if len(h) != 1 or len(b) != 1 or h[0] >= b[0]:
        return None, f"want one {AMENDMENTS_HEADING!r} heading line before the protocol BEGIN line (at {h} and {b})"
    return "\n".join(lines[h[0] + 1:b[0]]) + "\n", None


def load_amendments(path=None) -> "dict | None":
    """The amendments' JSON (PREREG "Amendments before data"), or None when that section holds no ```json block."""
    p = Path(path) if path else PREREG
    sec, why = amendments_block(p.read_text(encoding="utf-8"))
    if sec is None:
        raise ValueError(f"{p}: {why}")
    if not re.search(r"^```json$", sec, re.M):
        return None
    return json_in(sec)


def _laid_over(base, over):
    """`over` laid over `base`: dicts merged key by key, anything else (a list, a value) replaced; never in place."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _laid_over(base.get(k), v)
        return out
    return json.loads(json.dumps(over))


def amend(proto: dict, reg: dict, am: "dict | None") -> "tuple[dict, dict]":
    """(protocol, registered) with the amendments' `protocol` and `registered` parts laid over them (PREREG: "an
    amendment governs the items it names"; the locked blocks stay verbatim)."""
    am = am or {}
    return _laid_over(proto, am.get("protocol") or {}), _laid_over(reg, am.get("registered") or {})


def load_amended(path=None) -> "tuple[dict, dict, dict | None]":
    """(protocol, registered, amendments) of PREREG.md (or of the PREREG at `path`), the amendments laid over the two
    locked blocks: what every decision command measures against."""
    am = load_amendments(path)
    proto, reg = amend(load_protocol(path), load_registered(path), am)
    return proto, reg, am


def registered_kit_hash(path=None) -> "str | None":
    """The kit hash section 1 registers (PREREG A1), or None while its slot reads `<to be filled>` (or is missing)."""
    p = Path(path) if path else PREREG
    head = p.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n" + AMENDMENTS_HEADING + "\n")[0]
    found = KIT_SLOT.findall(head)
    return found[0] if len(found) == 1 and HEX64.match(found[0]) else None


def registration(args) -> "tuple[dict, str]":
    """(the registered JSON, its name): --registered FILE, else PREREG.md's block."""
    if getattr(args, "registered", None):
        with open(args.registered, encoding="utf-8") as fh:
            return json.load(fh), f"file:{Path(args.registered).name}"
    return load_registered(), "PREREG.md"


def source_problems(src: dict, *, register: bool = False) -> list:
    """What makes a registered source unusable (empty: none). `register`: the manifest fields may be null."""
    probs = []
    missing = SOURCE_KEYS - set(src)
    if missing:
        probs.append(f"missing {sorted(missing)}")
        return probs
    if src["layout"] not in LAYOUTS:
        probs.append(f"layout {src['layout']!r} is not one of {sorted(LAYOUTS)}")
    if not HEX40.match(str(src["commit"])):
        probs.append("commit is not 40 hex characters")
    if not (register and src["sql_manifest_sha256"] is None) and not HEX64.match(str(src["sql_manifest_sha256"])):
        probs.append("sql_manifest_sha256 is not 64 hex characters")
    for k in ("sql_files", "sql_bytes"):
        if not (register and src[k] is None) and not (type(src[k]) is int and src[k] > 0):
            probs.append(f"{k} is not a positive whole number")
    if not str(src["url"]).startswith("https://"):
        probs.append("url is not https")
    if src["layout"] == "raw" and not (HEX64.match(str(src.get("sha256", ""))) and src.get("path")):
        probs.append("a raw source needs `path` and a 64-hex `sha256`")
    if not isinstance(src.get("select", []), list):
        probs.append("`select` is not a list of path prefixes")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", str(src["id"])):
        probs.append("id is not a short lower-case slug")
    return probs


# ---------------------------------------------------------------------------------------------------- safety
def _norm(p) -> str:
    s = os.path.normcase(os.path.abspath(os.fspath(p)))
    return s.rstrip("\\/")


def inside_repo(path, repo=None) -> bool:
    """True when `path` (resolved; symbolic links followed where they exist) is this repository or lies inside it."""
    r = _norm(Path(repo or REPO).resolve())
    try:
        p = _norm(Path(path).resolve())
    except OSError:
        p = _norm(path)
    return p == r or p.startswith(r + os.sep)


def out_problem(path, repo=None) -> "str | None":
    """Why `path` may not receive corpus or result files (None: it may)."""
    if not str(path or "").strip():
        return "no output directory given"
    if inside_repo(path, repo):
        return (f"{path} is inside this repository ({repo or REPO}): the corpus and every result stay outside it "
                f"(a .sql file in the repository would enter its own graph)")
    return None


def _os_path(p) -> str:
    """The form of a path every file operation here uses: on Windows always the \\\\?\\ form of the absolute path, so
    a file past MAX_PATH is read and written and a walk never skips a long directory; unchanged elsewhere."""
    s = os.path.abspath(os.fspath(p))
    if os.name != "nt" or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


# ---------------------------------------------------------------------------------------------------- manifests
def is_sql(rel: str) -> bool:
    return rel.lower().endswith(".sql")


def manifest_lines(pairs) -> list:
    """`<sha256>  <rel>\\n` for each (rel, bytes), in the byte order of the UTF-8 paths."""
    rows = sorted(((rel.replace("\\", "/"), hashlib.sha256(data).hexdigest(), len(data)) for rel, data in pairs),
                  key=lambda r: r[0].encode("utf-8"))
    return [f"{h}  {rel}\n" for rel, h, _ in rows]


def manifest_of(pairs) -> dict:
    """{sql_manifest_sha256, sql_files, sql_bytes} of (rel, bytes) pairs."""
    pairs = list(pairs)
    lines = manifest_lines(pairs)
    return {"sql_manifest_sha256": hashlib.sha256("".join(lines).encode("utf-8")).hexdigest(),
            "sql_files": len(lines), "sql_bytes": sum(len(d) for _, d in pairs)}


def walk_sql(root) -> list:
    """(rel, Path) of every .sql file under `root` (any case), `.git` directories skipped, sorted by rel."""
    out = []
    base = _os_path(root)

    def fail(exc):                                      # a directory that cannot be listed is an error, never a gap
        raise exc
    for d, dirs, files in os.walk(base, onerror=fail):
        dirs[:] = sorted(x for x in dirs if x != ".git")
        for f in files:
            if is_sql(f):
                full = os.path.join(d, f)
                rel = os.path.relpath(full, base).replace("\\", "/")
                out.append((rel, full))
    return sorted(out, key=lambda r: r[0].encode("utf-8"))


def tree_manifest(root) -> dict:
    pairs = []
    for rel, full in walk_sql(root):
        with open(full, "rb") as fh:
            pairs.append((rel, fh.read()))
    res = manifest_of(pairs)
    res["longest_path"] = max((len(os.path.abspath(os.path.join(os.fspath(root), r))) for r, _ in pairs), default=0)
    return res


# ---------------------------------------------------------------------------------------------------- git / http
def _git_env() -> dict:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def git(cwd, *args, inp: "bytes | None" = None, check=True) -> bytes:
    cmd = ["git", "-c", "core.autocrlf=false", "-c", "core.longpaths=true", "-c", "core.quotepath=false", *args]
    r = subprocess.run(cmd, cwd=os.path.abspath(os.fspath(cwd)), input=inp, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=_git_env())
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} failed ({r.returncode}): "
                           f"{r.stderr.decode('utf-8', 'replace').strip()[:400]}")
    return r.stdout


def _git_init(path) -> None:
    os.makedirs(_os_path(path), exist_ok=True)
    if not os.path.isdir(_os_path(os.path.join(os.fspath(path), ".git"))):
        git(path, "init", "-q")
    git(path, "config", "core.autocrlf", "false")
    git(path, "config", "core.longpaths", "true")


def _fetch_commit(repo_dir, url: str, commit: str) -> None:
    _git_init(repo_dir)
    remotes = git(repo_dir, "remote").decode().split()
    if "origin" not in remotes:
        git(repo_dir, "remote", "add", "origin", url)
    git(repo_dir, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit)


def ls_tree(repo_dir, commit: str) -> list:
    """(mode, type, oid, path) of every entry of `commit`'s tree, recursively."""
    raw = git(repo_dir, "ls-tree", "-r", "-z", "--full-tree", commit)
    out = []
    for ent in raw.split(b"\0"):
        if not ent:
            continue
        meta, path = ent.split(b"\t", 1)
        mode, typ, oid = meta.decode().split()
        out.append((mode, typ, oid, path.decode("utf-8")))
    return out


def cat_blobs(repo_dir, oids: list) -> dict:
    """{oid: bytes} through one `git cat-file --batch`."""
    if not oids:
        return {}
    raw = git(repo_dir, "cat-file", "--batch", inp=("\n".join(oids) + "\n").encode())
    out, i = {}, 0
    for oid in oids:
        nl = raw.index(b"\n", i)
        head = raw[i:nl].decode().split()
        if len(head) != 3 or head[1] != "blob":
            raise RuntimeError(f"cat-file: {oid} is not a readable blob ({raw[i:nl][:80]!r})")
        size = int(head[2])
        out[oid] = raw[nl + 1:nl + 1 + size]
        i = nl + 1 + size + 1
    return out


def _write(path, data: bytes) -> None:
    p = _os_path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data)


def http_get(url: str, *, tries: int = 3) -> bytes:
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "deepmap-g-sql-fetch"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001  (retried, then reported)
            last = exc
            time.sleep(2 * (k + 1))
    raise RuntimeError(f"GET {url} failed: {type(last).__name__}: {last}")


# ---------------------------------------------------------------------------------------------------- per layout
def source_dir(out, src) -> Path:
    return Path(out) / src["id"]


def project_root(out, src) -> Path:
    """The directory DeepMap is pointed at for this source (C4, C5, C6) and whose .sql files form its SQL set."""
    d = source_dir(out, src)
    if src["layout"] == "clone":
        return d / "repo" / src.get("root", "")
    return d / "tree"


def _sql_entries(entries, src) -> list:
    prefixes = [p.rstrip("/") + "/" for p in src.get("select") or []]
    root = (src.get("root") or "").strip("/")
    out = []
    for mode, typ, oid, path in entries:
        if typ != "blob" or mode not in ("100644", "100755") or not is_sql(path):
            continue
        if prefixes and not any(path.startswith(p) for p in prefixes):
            continue
        if root and not path.startswith(root + "/"):
            continue
        out.append((path[len(root) + 1:] if root else path, oid))
    return out


def fetch_sql_set(out, src) -> dict:
    d = source_dir(out, src)
    helper, tree = d / "_git", d / "tree"
    _fetch_commit(helper, src["url"], src["commit"])
    pats = [f"/{p.strip('/')}/**/*.[sS][qQ][lL]" for p in src.get("select") or []] or ["/**/*.[sS][qQ][lL]"]
    git(helper, "sparse-checkout", "set", "--no-cone", *pats)
    git(helper, "checkout", "-q", "--detach", src["commit"])        # fetches the selected blobs in one batch
    sel = _sql_entries(ls_tree(helper, src["commit"]), src)
    blobs = cat_blobs(helper, [oid for _, oid in sel])
    for rel, oid in sel:
        _write(tree / rel, blobs[oid])
    _git_init(tree)
    return {"written": len(sel)}


def fetch_clone(out, src) -> dict:
    repo = source_dir(out, src) / "repo"
    _fetch_commit(repo, src["url"], src["commit"])
    if src.get("sparse"):
        git(repo, "sparse-checkout", "set", "--cone", *src["sparse"])
    git(repo, "checkout", "-q", "--detach", src["commit"])
    sel = _sql_entries(ls_tree(repo, src["commit"]), src)
    blobs = cat_blobs(repo, [oid for _, oid in sel])
    rewritten = 0
    root = project_root(out, src)
    for rel, oid in sel:
        p = root / rel
        try:
            with open(_os_path(p), "rb") as fh:
                same = fh.read() == blobs[oid]
        except OSError:
            same = False
        if not same:                                    # an eol attribute (or a missing file): the blob's bytes
            _write(p, blobs[oid])
            rewritten += 1
    return {"written": len(sel), "rewritten_from_blob": rewritten}


def fetch_raw(out, src) -> dict:
    tree = source_dir(out, src) / "tree"
    data = http_get(src["url"])
    got = hashlib.sha256(data).hexdigest()
    if got != src["sha256"]:
        raise RuntimeError(f"{src['url']}: sha256 {got} is not the registered {src['sha256']}")
    _write(tree / src["path"], data)
    _git_init(tree)
    return {"written": 1}


def verify_source(out, src) -> dict:
    """{ok, problems, manifest...} of a fetched source against its registration."""
    probs = []
    root = project_root(out, src)
    if not os.path.isdir(_os_path(root)):
        return {"ok": False, "problems": [f"{root} is missing"], "present": False}
    if src["layout"] == "clone":
        head = git(source_dir(out, src) / "repo", "rev-parse", "HEAD", check=False).decode().strip()
        if head != src["commit"]:
            probs.append(f"HEAD is {head or '?'}, registered {src['commit']}")
    if src["layout"] == "raw":
        try:
            with open(_os_path(root / src["path"]), "rb") as fh:
                data = fh.read()
            if hashlib.sha256(data).hexdigest() != src["sha256"] or len(data) != src.get("bytes", len(data)):
                probs.append(f"{src['path']}: sha256 / size differ from the registration")
        except OSError as exc:
            probs.append(f"{src['path']}: {exc}")
    man = tree_manifest(root) if src["layout"] != "clone" else _clone_sql_manifest(out, src)
    for k in ("sql_manifest_sha256", "sql_files", "sql_bytes"):
        if man.get(k) != src[k]:
            probs.append(f"{k} is {man.get(k)}, registered {src[k]}")
    if man.get("longest_path", 0) >= 248:
        say(f"note: {src['id']}: the longest file path is {man['longest_path']} characters; decision runs that hand "
            f"this tree to the engine need a shorter corpus directory (PREREG section 1)")
    return {"ok": not probs, "problems": probs, "present": True, **man}


def sql_files(out, src) -> list:
    """(rel, path) of a fetched source's SQL set, sorted by the UTF-8 bytes of rel: a clone's tracked .sql files under
    its root (git ls-tree of HEAD), every .sql file of a sql-set or raw tree. What verify hashes and run_corpus
    reads."""
    root = project_root(out, src)
    if src["layout"] != "clone":
        return walk_sql(root)
    rels = [rel for rel, _oid in _sql_entries(ls_tree(source_dir(out, src) / "repo", "HEAD"), src)]
    return sorted(((rel, _os_path(root / rel)) for rel in rels), key=lambda r: r[0].encode("utf-8"))


def _clone_sql_manifest(out, src) -> dict:
    """The manifest of a clone's SQL set: the tracked .sql files under its root, read from the work tree."""
    root = project_root(out, src)
    pairs = []
    for rel, full in sql_files(out, src):
        with open(full, "rb") as fh:
            pairs.append((rel, fh.read()))
    res = manifest_of(pairs)
    res["longest_path"] = max((len(os.path.abspath(os.path.join(os.fspath(root), r))) for r, _ in pairs), default=0)
    return res


FETCHERS = {"sql-set": fetch_sql_set, "clone": fetch_clone, "raw": fetch_raw}


# ---------------------------------------------------------------------------------------------------- select-ef
def _gh_env() -> dict:
    env = dict(os.environ)
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    return env


def gh_json(args: list, *, inp: "bytes | None" = None, tries: int = 6) -> dict:
    last = ""
    for k in range(tries):
        r = subprocess.run(["gh", "api", *args], input=inp, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=_gh_env())
        if r.returncode == 0:
            return json.loads(r.stdout.decode("utf-8"))
        try:                                            # GraphQL: a partial answer (a deleted or blocked repository
            body = json.loads(r.stdout.decode("utf-8"))  # among the ids) exits 1 but still carries `data`
            if isinstance(body, dict) and body.get("data") is not None:
                return body
        except ValueError:
            pass
        last = r.stderr.decode("utf-8", "replace").strip()[:300]
        if "rate limit" in last.lower() or "403" in last or "abuse" in last.lower() or "502" in last:
            time.sleep(65)
            continue
        time.sleep(5 * (k + 1))
    raise RuntimeError(f"gh api {' '.join(args[:3])} failed: {last}")


def _search(q: str, page: int) -> dict:
    time.sleep(7)                                       # REST code search: 10 requests a minute
    return gh_json(["-X", "GET", "search/code", "-f", f"q={q}", "-f", "per_page=100", "-f", f"page={page}"])


def code_hits(query: str, log) -> "tuple[list, dict]":
    """Every hit of `query`, enumerated in file-size partitions of at most 1,000 hits each."""
    hits, parts = {}, []
    stack = [(0, SIZE_MAX)]
    incomplete = False
    while stack:
        lo, hi = stack.pop()
        first = _search(f"{query} size:{lo}..{hi}", 1)
        total = int(first.get("total_count", 0))
        incomplete |= bool(first.get("incomplete_results"))
        if total > 1000 and hi > lo:
            mid = (lo + hi) // 2
            stack.extend([(mid + 1, hi), (lo, mid)])
            log(f"size {lo}..{hi}: {total} hits, split")
            continue
        items = list(first.get("items") or [])
        for page in range(2, min(10, (total + 99) // 100) + 1):
            items += list(_search(f"{query} size:{lo}..{hi}", page).get("items") or [])
        parts.append({"lo": lo, "hi": hi, "total": total, "got": len(items)})
        log(f"size {lo}..{hi}: {total} hits, {len(items)} read")
        for it in items:
            repo = it.get("repository") or {}
            hits[(repo.get("full_name"), it.get("path"))] = {"repo": repo.get("full_name"), "path": it.get("path"),
                                                             "node_id": repo.get("node_id"),
                                                             "fork": bool(repo.get("fork"))}
    return sorted(hits.values(), key=lambda h: (h["repo"] or "", h["path"] or "")), \
        {"partitions": sorted(parts, key=lambda p: p["lo"]), "incomplete_results": incomplete}


_GQL = ("query($ids:[ID!]!){nodes(ids:$ids){... on Repository{nameWithOwner stargazerCount isFork isArchived "
        "isPrivate defaultBranchRef{name target{oid}}}}}")


def repo_meta(node_ids: list) -> dict:
    out = {}
    ids = sorted(set(i for i in node_ids if i))
    for k in range(0, len(ids), 100):
        body = json.dumps({"query": _GQL, "variables": {"ids": ids[k:k + 100]}}).encode()
        res = gh_json(["graphql", "--input", "-"], inp=body)
        for n in (res.get("data") or {}).get("nodes") or []:
            if n and n.get("nameWithOwner"):
                ref = n.get("defaultBranchRef") or {}
                out[n["nameWithOwner"].lower()] = {
                    "repo": n["nameWithOwner"], "stars": int(n.get("stargazerCount") or 0),
                    "fork": bool(n.get("isFork")), "archived": bool(n.get("isArchived")),
                    "private": bool(n.get("isPrivate")), "branch": ref.get("name"),
                    "head": (ref.get("target") or {}).get("oid")}
    return out


def evaluate_candidate(meta: dict, paths: list) -> dict:
    """The metadata checks of one candidate at its default branch head (design 9: path present, .NET)."""
    res = {"repo": meta["repo"], "stars": meta["stars"], "head": meta["head"], "ok": False}
    if meta["repo"].lower() in EF_EXCLUDED:
        return dict(res, why="a D3-cs corpus")
    if meta["fork"] or meta["archived"] or meta["private"] or not meta["head"]:
        return dict(res, why="fork, archived, private or no default branch")
    tree = gh_json([f"repos/{meta['repo']}/git/trees/{meta['head']}?recursive=1"])
    blobs = [t["path"] for t in tree.get("tree") or [] if t.get("type") == "blob"]
    n_cs = sum(1 for p in blobs if p.lower().endswith(".cs"))
    res.update(cs_files=n_cs, truncated=bool(tree.get("truncated")),
               sql_files=sum(1 for p in blobs if is_sql(p)))
    if n_cs < EF_MIN_CS_FILES:
        return dict(res, why=f"{n_cs} .cs files (< {EF_MIN_CS_FILES})")
    sqls = sorted((p for p in blobs if is_sql(p)), key=lambda p: p.encode("utf-8"))
    hit = set(paths)
    order = [p for p in sqls if p in hit] + [p for p in sqls if p not in hit]      # the search's hits first
    for p in order[:EF_MAX_SQL_READ]:
        url = (f"https://raw.githubusercontent.com/{meta['repo']}/{meta['head']}/"
               + urllib.parse.quote(p, safe="/"))
        text = http_get(url).decode("utf-8", "replace")
        if EF_GUARD.search(text):
            return dict(res, ok=True, guard_file=p, sql_read=order.index(p) + 1)
    return dict(res, sql_read=min(len(order), EF_MAX_SQL_READ),
                why=f"no idempotent guard in the {min(len(order), EF_MAX_SQL_READ)} .sql file(s) read at the head")


def cmd_select_ef(args) -> int:
    out = Path(args.out)
    log_lines = []

    def log(m):
        log_lines.append(m)
        say(m)
    hits_file = out / "select_ef_hits.json"
    if args.hits:                                       # a previous search of the same query, reused
        saved = json.loads(Path(args.hits).read_text(encoding="utf-8"))
        if saved.get("query") != EF_QUERY:
            raise ValueError(f"{args.hits} holds another query ({saved.get('query')!r})")
        stamp, hits, meta_search = saved["searched_at"], saved["hits"], saved["search"]
    else:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        hits, meta_search = code_hits(EF_QUERY, log)
        os.makedirs(_os_path(out), exist_ok=True)
        with open(_os_path(hits_file), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"query": EF_QUERY, "searched_at": stamp, "hits": hits, "search": meta_search}, fh, indent=1,
                      sort_keys=True)
            fh.write("\n")
    log(f"{len(hits)} hits in {len({h['repo'] for h in hits})} repositories")
    metas = repo_meta([h["node_id"] for h in hits])
    by_repo: dict = {}
    for h in hits:
        by_repo.setdefault((h["repo"] or "").lower(), []).append(h["path"])
    ranked = sorted(metas.values(), key=lambda m: (-m["stars"], m["repo"].lower()))
    evaluated, chosen = [], None
    for m in ranked:
        ev = evaluate_candidate(m, sorted(by_repo.get(m["repo"].lower(), [])))
        evaluated.append(ev)
        log(f"{m['repo']} ({m['stars']} stars): {'SELECTED' if ev['ok'] else ev.get('why')}")
        if ev["ok"]:
            chosen = ev
            break
    rec = {"schema": "g-sql/select-ef/1", "searched_at": stamp, "query": EF_QUERY, "guard": EF_GUARD.pattern,
           "excluded": list(EF_EXCLUDED), "min_cs_files": EF_MIN_CS_FILES, "hits": len(hits),
           "repositories": len(metas), "search": meta_search, "evaluated": evaluated, "selected": chosen,
           "top_by_stars": [{"repo": m["repo"], "stars": m["stars"]} for m in ranked[:25]], "log": log_lines}
    os.makedirs(_os_path(out), exist_ok=True)
    with open(_os_path(out / "select_ef.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    say(f"selected: {chosen['repo'] if chosen else None}; wrote {out / 'select_ef.json'}")
    return 0 if chosen else 1


# ---------------------------------------------------------------------------------------------------- commands
def _only(reg: dict, only) -> list:
    srcs = list(reg.get("sources") or [])
    if only:
        want = {x.strip() for x in only.split(",") if x.strip()}
        unknown = want - {s.get("id") for s in srcs}
        if unknown:
            raise ValueError(f"unknown source id(s): {sorted(unknown)}")
        srcs = [s for s in srcs if s.get("id") in want]
    return srcs


def cmd_plan(args) -> int:
    reg, name = registration(args)
    for s in reg.get("sources") or []:
        probs = source_problems(s)
        say(f"{s.get('id')}: {s.get('layout')} {s.get('url')} @ {s.get('commit')} "
            f"({s.get('sql_files')} .sql files, {s.get('sql_bytes')} bytes)" + (f" PROBLEMS {probs}" if probs else ""))
    say(f"registration: {name}")
    return 0


def cmd_fetch(args, verify_only=False) -> int:
    why = out_problem(args.out)
    if why:
        say(f"REFUSED: {why}")
        return 2
    try:
        reg, name = registration(args)
        srcs = _only(reg, args.only)
    except (OSError, ValueError) as exc:
        say(f"REFUSED: {exc}")
        return 2
    register = bool(getattr(args, "register", False))
    if register and not getattr(args, "registered", None):
        say("REFUSED: --register needs --registered FILE (PREREG.md's registration is never rewritten)")
        return 2
    bad = {s.get("id"): source_problems(s, register=register) for s in srcs
           if source_problems(s, register=register)}
    if bad:
        say(f"REFUSED: unusable registration ({name}): {bad}")
        return 2
    rc = 0
    for s in srcs:
        if register:
            rc = max(rc, _register_one(args.out, s))
            continue
        res = verify_source(args.out, s)
        if res["ok"]:
            say(f"{s['id']}: present and verified ({res['sql_files']} .sql files, {res['sql_bytes']} bytes)")
            continue
        if verify_only or res["present"]:
            say(f"{s['id']}: verification FAILED: {res['problems']}"
                + ("" if verify_only else f" (remove {source_dir(args.out, s)} by hand to fetch it again)"))
            rc = max(rc, 1)
            continue
        try:
            info = FETCHERS[s["layout"]](args.out, s)
        except Exception as exc:  # noqa: BLE001  (a network or tool failure: reported, exit 3)
            say(f"{s['id']}: fetch FAILED: {type(exc).__name__}: {exc}")
            rc = max(rc, 3)
            continue
        res = verify_source(args.out, s)
        say(f"{s['id']}: fetched {info}; " + ("verified" if res["ok"] else f"verification FAILED: {res['problems']}"))
        if not res["ok"]:
            rc = max(rc, 1)
    say(f"registration: {name}; exit {rc}")
    return rc


def _register_one(out, s) -> int:
    """Registration time: fetch `s` unless its directory exists, then print its computed manifest."""
    if not os.path.isdir(_os_path(source_dir(out, s))):
        try:
            FETCHERS[s["layout"]](out, s)
        except Exception as exc:  # noqa: BLE001
            say(f"{s['id']}: fetch FAILED: {type(exc).__name__}: {exc}")
            return 3
    root = project_root(out, s)
    man = tree_manifest(root) if s["layout"] != "clone" else _clone_sql_manifest(out, s)
    say(f"{s['id']}: REGISTER {json.dumps(man, sort_keys=True)}")
    return 0


def cmd_manifest(args) -> int:
    res = tree_manifest(args.dir)
    print(json.dumps(res, sort_keys=True))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--registered")
    for name in ("fetch", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True)
        p.add_argument("--only")
        p.add_argument("--registered")
        if name == "fetch":
            p.add_argument("--register", action="store_true")
    p = sub.add_parser("manifest")
    p.add_argument("dir")
    p = sub.add_parser("select-ef")
    p.add_argument("--out", required=True)
    p.add_argument("--hits", help="reuse the select_ef_hits.json of an earlier search of the same query")
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        return cmd_plan(args)
    if args.cmd == "fetch":
        return cmd_fetch(args)
    if args.cmd == "verify":
        return cmd_fetch(args, verify_only=True)
    if args.cmd == "manifest":
        return cmd_manifest(args)
    why = out_problem(args.out)
    if why:
        say(f"REFUSED: {why}")
        return 2
    try:
        return cmd_select_ef(args)
    except Exception as exc:  # noqa: BLE001
        say(f"select-ef FAILED: {type(exc).__name__}: {exc}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
