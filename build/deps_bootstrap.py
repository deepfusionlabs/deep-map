#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepMap's own Python environment: `/deep-map:setup` installs DeepMap's packages itself (no terminal step).

The slash commands run bare `python`. When that Python lacks one of DeepMap's packages, or holds one outside the range
requirements.txt pins, DeepMap never pip-installs into it. Setup checks Java and free disk, creates (or repairs) a
virtual environment DeepMap owns, <DEEPMAP_HOME>/venv, installs requirements.txt there (torch from the PyTorch CPU
index, as the installers do), downloads the embedding model, runs scripts/ml_stack_selfcheck.py with it, then re-runs
itself under that interpreter. Every other entry point a slash command runs (each provision.py verb, deepmap_cli.py)
re-runs itself under it as well once the environment is complete and its interpreter can start; output and exit
codes pass through unchanged. A directory it did not make (no deepmap-venv.json) is never touched; one it made that no
longer works is moved aside and made afresh, never cleared in place and never while a process runs from it.

Standard library only: this runs before any third-party import, under whatever Python found the script. A Python that
already satisfies requirements.txt (an installer user) costs one listing of its sys.path directories, nothing more.

  requirements()  the specs, read from requirements.txt: the one copy of the ranges (the installers, CI and
                  tests/compliance_gates.py read the same file).
  missing()       what this interpreter lacks or holds out of range: a scan of *.dist-info names (importlib.metadata
                  alone costs ~0.4 s on a Store-Python Windows box, too much for every command).
  entry()         provision.py's __main__: `setup` -> setup_entry(), every other verb -> reexec_entry().
  reexec_entry()  deepmap_cli.py's __main__ (and provision's verbs).

Env: DEEPMAP_MANAGED_VENV=0 never creates or uses the environment (setup then fails on a Python without the packages,
saying so). DEEPMAP_REEXEC_GUARD=1, set in every re-run child, is the loop guard. Besides pip, the one network step is
the model download, the only step run with HF_HUB_OFFLINE lifted.
Internal: `python build/deps_bootstrap.py --probe` prints this interpreter's missing requirements as JSON.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REQUIREMENTS = ROOT / "requirements.txt"
SELFCHECK = ROOT / "scripts" / "ml_stack_selfcheck.py"

GUARD_ENV = "DEEPMAP_REEXEC_GUARD"
OPTOUT_ENV = "DEEPMAP_MANAGED_VENV"
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"     # the CPU build, as install.sh / install.ps1 install it
MODEL_IGNORE = ("onnx/*", "openvino/*")                  # ~1.9 GB of exports sentence-transformers never loads
# The files SentenceTransformer reads for embeddings.MODEL_NAME (nomic-ai/modernbert-embed-base), besides each
# module's own config (<path>/config.json, from modules.json; Normalize has none). Change with the model.
MODEL_KEY_FILES = ("modules.json", "config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
                   "special_tokens_map.json", "sentence_bert_config.json", "config_sentence_transformers.json")
MIN_PYTHON = (3, 10)
VENV_NAME = "venv"
MARKER_NAME = "deepmap-venv.json"
LOG_NAME = "venv-setup.log"

# Free disk the first setup needs, in GB, with headroom. Measured 2026-10-04 (Windows 11, Python 3.13): the
# environment 1.28 GB, pip's download cache 0.25 GB (plus pip's temp files while it installs), the model 0.60 GB.
# NEO4J_GB == provision.preflight's floor for the Neo4j download + store (0.33 GB measured).
VENV_GB = 2.0
MODEL_GB = 0.7
NEO4J_GB = 1.5
WIN_MAX_PATH = 260
WIN_VENV_LONGEST = 140   # deepest file pip writes below site-packages: 126 characters measured (transformers)

HEARTBEAT_S = 60
VENV_TIMEOUT_S = 900
PIP_TIMEOUT_S = 3600
MODEL_TIMEOUT_S = 3600
SELFCHECK_TIMEOUT_S = 2700
PROBE_TIMEOUT_S = 180
LOCK_WAIT_S = 7200

_PIP_FLAGS = ("--disable-pip-version-check", "--no-input", "--progress-bar", "off")


# ---- requirements and what this interpreter has (fast, no third-party code) -------------------------------------
_REQ_LINE = re.compile(r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[(?P<extras>[^\]]*)\])?\s*"
                       r"(?P<spec>[^;]*?)\s*(?:;\s*(?P<marker>.*?))?\s*$")
_CLAUSE = re.compile(r"(===|~=|==|!=|<=|>=|<|>)\s*([A-Za-z0-9_.!+*-]+)$")


def parse_requirement(line):
    """One requirements.txt line in the subset DeepMap reads — `name[extras] clauses ; marker`, with a marker
    _marker_true can decide — as {"name", "extras", "spec", "applies"}; None for anything else (a URL, an option,
    an unknown marker). spec: the clauses without whitespace ('' for none); extras: '[a,b]' or ''."""
    m = _REQ_LINE.match(str(line).strip())
    if not m:
        return None
    raw = m.group("spec").strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip()
    clauses = [c.strip() for c in raw.split(",")] if raw else []
    if not all(_CLAUSE.match(c) for c in clauses):
        return None
    marker = (m.group("marker") or "").strip()
    applies = _marker_true(marker) if marker else True
    if applies is None:
        return None
    extras = [x.strip() for x in (m.group("extras") or "").split(",") if x.strip()]
    return {"name": m.group("name"), "extras": f"[{','.join(extras)}]" if extras else "",
            "spec": ",".join(re.sub(r"\s+", "", c) for c in clauses), "applies": applies}


def requirements(path=None) -> list:
    """[(name, spec)] for each requirement of requirements.txt that applies here, in file order. spec: the extras
    ('[a,b]', kept for pip) + the version clauses, no whitespace; '' for an unpinned package. pip's reading: a UTF-8
    BOM is skipped, a line whose environment marker is false here is left out. A line outside the subset DeepMap
    reads (parse_requirement) is left out too; tests/setup_bootstrap_gates.py fails on one in the shipped file. []
    when the file can't be read: then nothing counts as missing (the old behaviour)."""
    try:
        text = Path(path or REQUIREMENTS).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        req = parse_requirement(line)
        if req and req["applies"]:
            out.append((req["name"], req["extras"] + req["spec"]))
    return out


_MARKER_TOKEN = re.compile(r"""\s*(?:(?P<str>'[^']*'|"[^"]*")|"""
                           r"""(?P<op>===|==|!=|<=|>=|~=|<|>|not\s+in(?![\w.])|in(?![\w.]))|"""
                           r"""(?P<paren>[()])|(?P<word>[A-Za-z_][A-Za-z0-9_.]*))""")
_VERSION_VARS = ("python_version", "python_full_version", "implementation_version")
_SWAPPED = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}


def _marker_env() -> dict:
    import platform
    impl = sys.implementation
    iv = impl.version
    iv_text = f"{iv.major}.{iv.minor}.{iv.micro}" + ("" if iv.releaselevel == "final" else
                                                     f"{iv.releaselevel[0]}{iv.serial}")
    return {"os_name": os.name, "sys_platform": sys.platform, "platform_system": platform.system(),
            "platform_machine": platform.machine(), "platform_release": platform.release(),
            "python_version": "%d.%d" % sys.version_info[:2], "python_full_version": platform.python_version(),
            "implementation_name": impl.name, "implementation_version": iv_text,
            "platform_python_implementation": platform.python_implementation()}


def _marker_true(text):
    """A PEP 508 environment marker of the plain kind — `<variable> <op> "<value>"` joined by and / or, with
    parentheses — evaluated here; None when it holds anything else (an `extra`, an unknown variable, bad syntax).
    Never raises."""
    try:
        s, pos, toks = str(text), 0, []
        while s[pos:].strip():
            m = _MARKER_TOKEN.match(s, pos)
            if not m or m.end() == pos:
                return None
            pos = m.end()
            val = m.group(m.lastgroup)
            toks.append((m.lastgroup, re.sub(r"\s+", " ", val) if m.lastgroup == "op" else val))
        env, i = _marker_env(), [0]

        def peek():
            return toks[i[0]] if i[0] < len(toks) else (None, None)

        def take():
            t = peek()
            i[0] += 1
            return t

        def value():
            kind, v = take()
            if kind == "str":
                return False, v[1:-1]
            if kind == "word" and v in env:
                return True, v
            raise ValueError(v)

        def atom():
            if peek() == ("paren", "("):
                take()
                r = expr()
                if take() != ("paren", ")"):
                    raise ValueError("(")
                return r
            (lvar, left), (kind, op), (rvar, right) = value(), take(), value()
            if kind != "op" or lvar == rvar:
                raise ValueError(op)
            var = left if lvar else right
            lhs, rhs = (env[left] if lvar else left), (env[right] if rvar else right)
            if op in ("in", "not in"):
                return (lhs in rhs) == (op == "in")
            if var in _VERSION_VARS and op != "===":
                if not lvar:
                    if op not in _SWAPPED:
                        raise ValueError(op)
                    lhs, rhs, op = rhs, lhs, _SWAPPED[op]
                return version_ok(op + rhs, lhs)
            if op in ("==", "!=", "==="):
                return (lhs == rhs) == (op != "!=")
            raise ValueError(op)

        def conj():
            r = atom()
            while peek() == ("word", "and"):
                take()
                r = atom() and r
            return r

        def expr():
            r = conj()
            while peek() == ("word", "or"):
                take()
                r = conj() or r
            return r
        result = expr()
        return bool(result) if i[0] == len(toks) else None
    except Exception:
        return None


def _canon(name) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


_PEP440 = re.compile(r"""^\s*v?(?:(?P<epoch>[0-9]+)!)?(?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>[-_.]?(?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?P<post>-(?P<post_n1>[0-9]+)|[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n2>[0-9]+)?)?
    (?P<dev>[-_.]?dev[-_.]?(?P<dev_n>[0-9]+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?\s*$""", re.X | re.I)
_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}


def _version(text):
    """A PEP 440 version as (epoch, release, pre, post, dev, local): pre is (rank, n) with a < b < rc, post / dev an
    int or None, local normalised; None when `text` is not one."""
    m = _PEP440.match(str(text))
    if not m:
        return None
    pre = (_PRE_RANK[m.group("pre_l").lower()], int(m.group("pre_n") or 0)) if m.group("pre_l") else None
    post = int(m.group("post_n1") or m.group("post_n2") or 0) if m.group("post") else None
    dev = int(m.group("dev_n") or 0) if m.group("dev") else None
    local = re.sub(r"[-_]", ".", m.group("local")).lower() if m.group("local") else None
    return int(m.group("epoch") or 0), tuple(int(x) for x in m.group("release").split(".")), pre, post, dev, local


def _base(v) -> tuple:
    rel = list(v[1])
    while rel and rel[-1] == 0:
        rel.pop()
    return v[0], tuple(rel)


def _key(v) -> tuple:
    """PEP 440 ordering of a parsed version, its local label left out: dev < a < b < rc < final < post."""
    epoch, rel = _base(v)
    pre, post, dev = v[2], v[3], v[4]
    pre_k = (-1, 0) if (pre is None and post is None and dev is not None) else ((3, 0) if pre is None else pre)
    return epoch, rel, pre_k, -1 if post is None else post, float("inf") if dev is None else dev


def _canonical(v) -> str:
    s = (f"{v[0]}!" if v[0] else "") + ".".join(map(str, v[1]))
    if v[2]:
        s += ("a", "b", "rc")[v[2][0]] + str(v[2][1])
    if v[3] is not None:
        s += f".post{v[3]}"
    if v[4] is not None:
        s += f".dev{v[4]}"
    return s + (f"+{v[5]}" if v[5] else "")


def _prefix_match(v, raw) -> bool:
    p = _version(raw)
    if p is None:
        return False
    n = len(p[1])
    return v[0] == p[0] and (tuple(v[1]) + (0,) * n)[:n] == p[1]


def _clause_ok(op, raw, v) -> bool:
    if op == "===":
        return _canonical(v) == raw.strip().lower()
    if raw.endswith(".*"):
        return op in ("==", "!=") and _prefix_match(v, raw[:-2]) == (op == "==")
    s = _version(raw)
    if s is None:
        return False
    if op == "~=":   # >=V plus ==<V without its last release number>.*
        head = (f"{s[0]}!" if s[0] else "") + ".".join(map(str, s[1][:-1]))
        return len(s[1]) > 1 and _key(v) >= _key(s) and _prefix_match(v, head)
    if op in ("==", "!="):
        same = _key(v) == _key(s) and (s[5] is None or v[5] == s[5])
        return same == (op == "==")
    if op == "<=":
        return _key(v) <= _key(s)
    if op == ">=":
        return _key(v) >= _key(s)
    is_pre = v[2] is not None or v[4] is not None
    if op == "<":   # never a pre-release of the bound itself (<7 rejects 7.0rc1)
        return _key(v) < _key(s) and not (is_pre and s[2] is None and s[4] is None and _base(v) == _base(s))
    if op == ">":   # never a post-release or a local build of the bound itself
        return (_key(v) > _key(s) and not (v[3] is not None and s[3] is None and _base(v) == _base(s))
                and not (v[5] is not None and _base(v) == _base(s)))
    return False


def _clauses(spec) -> str:
    """The version clauses of a requirements() spec, its leading [extras] left out."""
    spec = str(spec or "").strip()
    if spec.startswith("["):
        return spec[spec.find("]") + 1:].strip() if "]" in spec else ""
    return spec


def version_ok(spec: str, version: str) -> bool:
    """Does `version` satisfy `spec`? pip's answer for an INSTALLED version (PEP 440, pre-releases allowed, as pip
    allows them for what is installed): ==, !=, <, <=, >, >=, ~=, ===, ==X.* / !=X.*; a leading [extras] is skipped.
    No `packaging` import: it runs on every DeepMap command."""
    spec = _clauses(spec)
    if not spec:
        return True
    v = _version(version)
    if v is None:
        return False
    for clause in [c.strip() for c in spec.split(",") if c.strip()]:
        m = _CLAUSE.match(clause)
        if not m or not _clause_ok(m.group(1), m.group(2), v):
            return False
    return True


def _meta_name_version(path):
    """(name, version) from the metadata of a *.dist-info / *.egg-info entry whose name carries no version (a legacy
    editable install leaves `<name>.egg-info`): its METADATA / PKG-INFO, or the entry itself when it is that file.
    ('', '') when unreadable. Never raises."""
    try:
        p = Path(path)
        if p.is_dir():
            p = next((p / f for f in ("METADATA", "PKG-INFO") if (p / f).is_file()), None)
            if p is None:
                return "", ""
        name = ver = ""
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    break   # the headers end at the first blank line
                key, sep, val = line.partition(":")
                key = key.strip().lower()
                if sep and key == "name" and not name:
                    name = val.strip()
                elif sep and key == "version" and not ver:
                    ver = val.strip()
        return name, ver
    except Exception:
        return "", ""


def installed_versions(paths=None) -> dict:
    """{canonical name: version} from the *.dist-info / *.egg-info entries in `paths` (default: sys.path), the first
    copy on the path winning, as for an import. The version comes from the entry's name; only an entry whose name has
    none costs a read of its metadata. Never raises."""
    found: dict = {}
    for entry in (sys.path if paths is None else paths):
        try:
            d = entry or os.getcwd()
            if not os.path.isdir(d):
                continue
            names = os.listdir(d)
        except Exception:
            continue
        for n in names:
            low = n.lower()
            if low.endswith(".dist-info"):
                name, sep, ver = n[:-10].rpartition("-")
            elif low.endswith(".egg-info"):
                m = re.match(r"^(.+?)-(\d[^-]*)(?:-py\d.*)?$", n[:-9])
                name, sep, ver = (m.group(1), "-", m.group(2)) if m else ("", "", "")
            else:
                continue
            if not (sep and name and ver):
                meta_name, ver = _meta_name_version(os.path.join(d, n))
                name = meta_name or n.rsplit(".", 1)[0]
            if name and ver:
                found.setdefault(_canon(name), ver)
    return found


def missing(reqs=None, paths=None) -> list:
    """[{name, spec, found}] for each requirement this interpreter lacks (found None) or holds outside its spec;
    [] when it has everything."""
    reqs = requirements() if reqs is None else reqs
    have = installed_versions(paths)
    out = []
    for name, spec in reqs:
        ver = have.get(_canon(name))
        if ver is None or not version_ok(spec, ver):
            out.append({"name": name, "spec": spec, "found": ver})
    return out


def _describe(miss) -> str:
    parts = []
    for m in miss or []:
        if m.get("found"):
            parts.append(f"{m['name']} {m['found']} (needs {m['spec']})")
        else:
            parts.append(str(m.get("name")))
    return ", ".join(parts)


# ---- the managed environment ----------------------------------------------------------------------------------------
def home_dir(env=None) -> Path:
    """DEEPMAP_HOME (~/.deepmap by default): provision.deepmap_home's rule."""
    e = os.environ if env is None else env
    return Path(e.get("DEEPMAP_HOME", str(Path.home() / ".deepmap")))


def venv_dir(env=None) -> Path:
    return Path(os.path.abspath(home_dir(env) / VENV_NAME))


def venv_python(vdir, windows=None) -> Path:
    win = (os.name == "nt") if windows is None else bool(windows)
    return Path(vdir) / ("Scripts" if win else "bin") / ("python.exe" if win else "python")


def log_path(env=None) -> Path:
    return Path(os.path.abspath(home_dir(env) / LOG_NAME))


def _read_marker(vdir):
    try:
        data = json.loads((Path(vdir) / MARKER_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _cfg_values(path) -> dict:
    """pyvenv.cfg's settings as Python reads them ({key: value}, keys lower-cased, the first of a key winning, a BOM
    skipped); {} when unreadable."""
    out: dict = {}
    try:
        for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
            key, sep, val = line.partition("=")
            if sep:
                out.setdefault(key.strip().lower(), val.strip())
    except Exception:
        pass
    return out


_CFG_SPACE = b" \t\n\r\x0b\x0c"
_FREE_THREADED_EXE = re.compile(r"python3\.\d+t\.exe", re.I)


def _launcher_home(raw: bytes):
    """The `home` Windows' venv launcher reads in pyvenv.cfg's bytes (CPython PC/venvlauncher.c, find_home_value):
    the first "home" in the file, then each one starting a line, case-sensitive, followed by optional whitespace and
    "="; the value starts past the "=" and the whitespace after it (line ends included) and runs to the end of its
    line, trailing whitespace trimmed. A BOM before it changes nothing. None when there is none or it is empty (the
    launcher exits 107)."""
    buf = raw[:8191].split(b"\0", 1)[0]   # its 8 KB read, as a C string
    s = buf.find(b"home")
    while s != -1:
        i = s + 4
        while i < len(buf) and buf[i] in _CFG_SPACE:
            i += 1
        if buf[i:i + 1] == b"=":
            i += 1
            while i < len(buf) and buf[i] in _CFG_SPACE:
                i += 1
            end = buf.find(b"\n", i)
            if end == -1:
                end = len(buf)
            else:
                while end > i and buf[end - 1] in _CFG_SPACE:
                    end -= 1
            return buf[i:end].decode("utf-8", "replace") or None
        s = buf.find(b"\nhome", i + 1)
        s = s + 1 if s != -1 else -1
    return None


def _interpreter_present(vdir) -> bool:
    """Does the venv's interpreter start AS this environment? Judged without starting it, the way this OS does it.
    Windows: as its launcher (Scripts\\python.exe) does — pyvenv.cfg next to it, else the venv's (exit 106 without
    one), a `home` it can read there (_launcher_home; exit 107), and in that directory the interpreter it runs:
    python.exe, or for a free-threaded venv the python3.Xt.exe pyvenv.cfg's `executable` names (exit 103 when it is
    gone: an uninstalled base Python can leave its directory behind).
    POSIX: bin/python resolves (a link to a removed base Python does not) and pyvenv.cfg is there (without it the
    python starts as its base Python, outside the environment). `home` plays no part: site.py activates the venv
    whether it is missing, empty, relative or gone (CPython 3.10-3.14, measured 2026-10-05). Never raises."""
    try:
        v = Path(vdir)
        py = venv_python(v)
        if not py.is_file():   # follows links
            return False
        cfgs = [py.parent / "pyvenv.cfg", v / "pyvenv.cfg"]
        if os.name != "nt":
            return any(c.is_file() for c in cfgs)
        cfg = next((c for c in cfgs if c.exists()), None)
        home = _launcher_home(cfg.read_bytes()) if cfg is not None else None
        if not home:
            return False
        exe = re.split(r"[\\/]", _cfg_values(cfg).get("executable") or "")[-1]
        return os.path.isfile(os.path.join(home, exe if _FREE_THREADED_EXE.fullmatch(exe) else "python.exe"))
    except Exception:
        return False


def managed_python(env=None):
    """The managed environment's interpreter when it is COMPLETE (its marker says setup finished it and its self-check
    passed — a repair still pending keeps it so: the commands used it before that repair) and can start
    (_interpreter_present); else None, and the command runs under the Python that started it: doctor then names
    /deep-map:setup, and the mcp-stop a setup refusal names works. Never raises."""
    try:
        vdir = venv_dir(env)
        mark = _read_marker(vdir)
        if not mark or mark.get("complete") is not True:
            return None
        if not _interpreter_present(vdir):
            return None
        return str(venv_python(vdir))
    except Exception:
        return None


def _write_marker(vdir, *, complete: bool, python_version=None, pending: bool = False, finished_against=None) -> None:
    """pending (with complete): a setup on this environment did not finish; the commands keep using it, and the next
    setup does not reuse it as is. finished_against: the requirements.txt digest to record, today's by default — a
    pending repair keeps the one the environment was last finished against, so stale_note still names what it lacks."""
    try:
        model = _model_name()
    except Exception:
        model = None
    digest = finished_against
    if digest is None:
        try:
            digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
        except Exception:
            digest = None
    data = {"complete": bool(complete), "repair_pending": bool(pending), "python": str(venv_python(vdir)),
            "made_with": sys.executable, "python_version": python_version, "requirements_sha256": digest,
            "model": model, "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = Path(vdir) / MARKER_NAME
    tmp = path.with_name(f"{MARKER_NAME}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _set_incomplete(vdir) -> None:
    mark = _read_marker(vdir)
    if mark is not None and mark.get("complete") is not False:
        _write_marker(vdir, complete=False, python_version=mark.get("python_version"))


def _model_name() -> str:
    import embeddings   # build/embeddings.py: stdlib at import; its MODEL_NAME is the model DeepMap loads
    return embeddings.MODEL_NAME


def _hf_hub_cache(env) -> Path:
    """huggingface_hub's cache directory by its own rule: HF_HUB_CACHE, else HUGGINGFACE_HUB_CACHE, else
    <HF_HOME>/hub, HF_HOME defaulting to <XDG_CACHE_HOME or ~/.cache>/huggingface."""
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if env.get(key):
            return Path(os.path.expandvars(os.path.expanduser(env[key])))
    hf_home = env.get("HF_HOME") or os.path.join(env.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"), "huggingface")
    return Path(os.path.expandvars(os.path.expanduser(hf_home))) / "hub"


def _model_cache_dir(env) -> Path:
    """The embedding model's directory in the Hugging Face cache (models--<org>--<name>)."""
    return _hf_hub_cache(env) / ("models--" + _model_name().replace("/", "--"))


def _model_cached_guess(env) -> bool:
    """Is the model's weights file in the Hugging Face cache? A stdlib look at the cache layout, used only to plan the
    disk check before the environment exists; the decision itself asks huggingface_hub (_model_cached)."""
    try:
        snaps = _model_cache_dir(env) / "snapshots"
        return any((s / "model.safetensors").is_file() for s in snaps.iterdir())
    except Exception:
        return False


# ---- process seams (gates replace these) ----------------------------------------------------------------------------
def _say(msg: str = "", *, err: bool = False) -> None:
    try:
        print(f"[provision] {msg}", file=sys.stderr if err else sys.stdout, flush=True)
    except Exception:
        pass


def _dur(seconds: float) -> str:
    s = int(round(max(0.0, seconds)))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def _python_version() -> tuple:
    return tuple(sys.version_info[:3])


def _disk_free_bytes(path) -> int:
    return shutil.disk_usage(str(path)).free


def _run_step(key, argv, *, env, log, what, timeout_s):
    """Run one step with its output appended to the open log; a progress line every HEARTBEAT_S while it runs; stopped
    after `timeout_s`. (rc, seconds): rc None = it could not start, -1 = it ran past its limit and was stopped."""
    t0 = time.monotonic()
    try:
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {what}\n$ {' '.join(map(str, argv))}\n"
                  .encode("utf-8", "replace"))
        log.flush()
        proc = subprocess.Popen([str(a) for a in argv], stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env)
    except OSError as exc:
        try:
            log.write(f"could not start: {exc}\n".encode("utf-8", "replace"))
            log.flush()
        except Exception:
            pass
        return None, 0.0
    deadline = t0 + timeout_s
    while True:
        try:
            rc = proc.wait(timeout=max(0.1, min(HEARTBEAT_S, deadline - time.monotonic())))
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                proc.kill()   # our own child, still held: never a recorded pid
                proc.wait()
                rc = -1
                break
            _say(f"      ... still {what} ({_dur(time.monotonic() - t0)} so far)")
    secs = time.monotonic() - t0
    try:
        log.write(f"===== exit {rc} after {_dur(secs)}\n".encode("utf-8"))
        log.flush()
    except Exception:
        pass
    return rc, secs


def _capture(key, argv, *, env, timeout_s):
    """(rc, stdout) of a short helper run; (None, '') when it could not start, (-1, '') when it ran past its limit."""
    try:
        p = subprocess.run([str(a) for a in argv], capture_output=True, stdin=subprocess.DEVNULL, env=env,
                           timeout=timeout_s)
        return p.returncode, (p.stdout or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return -1, ""
    except OSError:
        return None, ""


def _flush() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except Exception:
            pass


def _spawn_child(argv, env):
    """Run `argv` as this process's continuation: POSIX replaces the process (os.execve: same pid, signals and exit
    code go straight to it); Windows has no such exec, so it waits for a child that shares this console and returns
    its exit code. None when it can't be started (the caller runs in-process instead)."""
    _flush()
    if os.name != "nt":
        try:
            os.execve(argv[0], argv, env)
        except OSError:
            return None
    try:
        proc = subprocess.Popen(argv, env=env)
    except OSError:
        return None
    while True:
        try:
            rc = proc.wait()
            break
        except KeyboardInterrupt:
            continue   # the child got the same Ctrl+C; its own exit code is the answer
    return rc if rc >= 0 else 128 - rc


# ---- re-running under the managed interpreter -----------------------------------------------------------------------
def opted_out(env) -> bool:
    return str(env.get(OPTOUT_ENV, "")).strip().lower() in ("0", "false", "no", "off")


def _same_file(a, b) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except Exception:
        return False


def child_env(env=None) -> dict:
    """The re-run child's environment: the loop guard on; no PYTHONPATH / PYTHONHOME (the managed environment sees only
    its own packages) and no __PYVENV_LAUNCHER__ (a venv launcher's marker belongs to the interpreter it started)."""
    e = dict(os.environ if env is None else env)
    e[GUARD_ENV] = "1"
    for key in ("PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__"):
        e.pop(key, None)
    return e


def reexec(python, script, argv, *, env=None):
    """Run `script argv` under `python`; its exit code (POSIX: never returns), or None when it could not start."""
    flags = ["-X", "utf8"] if getattr(sys.flags, "utf8_mode", 0) else []
    return _spawn_child([str(python), *flags, str(script), *map(str, argv)], child_env(env))


def _venv_site_dirs(vdir) -> list:
    """The environment's site-packages directories, found without starting its interpreter."""
    v = Path(vdir)
    if os.name == "nt":
        return [str(v / "Lib" / "site-packages")]
    return [str(p) for p in sorted(v.glob("lib/python*/site-packages"))]


def stale_note(env=None):
    """One line for stderr when the complete environment was finished against another requirements.txt (a plugin
    update raised a floor or added a package) and lacks or holds out of range one of this one's requirements; None
    otherwise. Costs one hash of requirements.txt; never raises."""
    try:
        vdir = venv_dir(env)
        recorded = (_read_marker(vdir) or {}).get("requirements_sha256")
        if not recorded or recorded == hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest():
            return None
        dirs = [d for d in _venv_site_dirs(vdir) if os.path.isdir(d)]
        lacks = missing(paths=dirs) if dirs else []
        if not lacks:
            return None
        return (f"[deepmap] NOTE: DeepMap's environment ({vdir}) predates this version's requirements "
                f"({_describe(lacks)}) — run /deep-map:setup to update it.")
    except Exception:
        return None


def reexec_entry(script, argv, *, env=None):
    """Every entry point but setup: re-run under the managed interpreter when this one lacks a requirement and the
    environment is complete. None = run here (as before): the loop guard or the opt-out is set, this Python has
    everything, there is no complete environment or its interpreter cannot start, or it is this very interpreter.
    Prints nothing, except stale_note's one stderr line before a re-run."""
    e = os.environ if env is None else env
    try:
        if e.get(GUARD_ENV) == "1" or opted_out(e):
            return None
        if not missing():
            return None
        py = managed_python(e)
        if not py or _same_file(py, sys.executable):
            return None
        note = stale_note(e)
        if note:
            print(note, file=sys.stderr, flush=True)
        return reexec(py, script, argv, env=e)
    except Exception:
        return None


def entry(script, argv, *, checks=None, env=None):
    """provision.py's __main__: `setup` (the default verb) builds the environment when needed (checks: a SetupChecks
    or a callable returning one); every other verb only re-runs under it. None = run main() here."""
    verb = argv[0] if argv else "setup"
    if verb == "setup" and checks is not None:
        return setup_entry(script, argv, checks=checks, env=env)
    return reexec_entry(script, argv, env=env)


class SetupChecks:
    """What provision lends the bootstrap, so no rule exists twice: setup's census (nothing to index -> nothing is
    installed either), preflight's Java rule, whether the Neo4j binary is still to be downloaded, its OS file lock and
    its Windows long-path probe. Each is optional."""

    def __init__(self, *, census=None, java_problems=None, neo4j_download_needed=None, file_lock=None,
                 file_unlock=None, long_paths_enabled=None):
        self.census = census
        self.java_problems = java_problems
        self.neo4j_download_needed = neo4j_download_needed
        self.file_lock = file_lock
        self.file_unlock = file_unlock
        self.long_paths_enabled = long_paths_enabled


def _utf8_streams() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", line_buffering=True)
        except Exception:
            pass


def setup_entry(script, argv, *, checks, env=None):
    """`provision.py setup` under a Python that lacks a requirement: build or repair the managed environment, then
    re-run setup under it and return its exit code (0 / 1 / 4) unchanged. None = run setup here (the loop guard or the
    opt-out is set, or this Python has everything — exactly as before); 1 = the environment could not be made (a
    FAILED verdict was printed)."""
    e = os.environ if env is None else env
    if e.get(GUARD_ENV) == "1" or opted_out(e):
        return None
    try:
        miss = missing()
    except Exception:
        return None
    if not miss:
        return None
    _utf8_streams()
    target = argv[1] if len(argv) > 1 else "."
    try:
        chk = checks() if callable(checks) else checks
        py = ensure_env(chk, target=target, env=e, script=script, here_missing=miss)
    except Exception as exc:  # noqa: BLE001 — setup ends with a verdict, never a raw traceback
        _say(f"setup verdict: FAILED — DeepMap's own Python environment could not be installed: {type(exc).__name__}: "
             f"{exc}. Fix the cause, then re-run /deep-map:setup.", err=True)
        return 1
    if not py:
        return 1
    _say(f"continuing setup under DeepMap's environment ({py})")
    rc = reexec(py, script, argv, env=e)
    if rc is None:
        _say(f"ERROR: DeepMap's environment could not be started ({py}).", err=True)
        _say("setup verdict: FAILED — DeepMap's own Python environment was installed but does not start; delete "
             f"{venv_dir(e)} and re-run /deep-map:setup.", err=True)
        return 1
    return rc


# ---- building / repairing the environment ---------------------------------------------------------------------------
def install_commands(py, reqs, *, torch=True) -> list:
    """[(key, argv)] — the pip commands that put requirements.txt into the environment `py` runs, in the installers'
    order: pip itself, torch from the CPU index, the unpinned packages, then the version-ranged ones with --upgrade
    (pip otherwise keeps an older copy that satisfies a spec)."""
    head = [str(py), "-m", "pip", "install", *_PIP_FLAGS]
    torch_spec = next((n + s for n, s in reqs if _canon(n) == "torch"), "torch")
    bare = [n + s for n, s in reqs if not _clauses(s) and _canon(n) != "torch"]
    ranged = [n + s for n, s in reqs if _clauses(s) and _canon(n) != "torch"]
    cmds = [("pip", head + ["--upgrade", "pip"])]
    if torch:
        cmds.append(("torch", head + ["--index-url", TORCH_INDEX, torch_spec]))
    if bare:
        cmds.append(("packages", head + bare))
    if ranged:
        cmds.append(("pinned", head + ["--upgrade", *ranged]))
    return cmds


def _tool_env(env, *, offline=None) -> dict:
    """The environment for a bootstrap helper: the caller's, minus what would point the interpreter or pip elsewhere
    (PYTHONPATH / PYTHONHOME / a venv launcher's marker, pip's --python / --target / --prefix / --root, a user-site
    install). offline=True forces HF_HUB_OFFLINE=1; offline=False lifts it (the model download alone)."""
    e = dict(env)
    for key in ("PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__", "PIP_PYTHON", "PIP_TARGET", "PIP_PREFIX",
                "PIP_ROOT"):
        e.pop(key, None)
    e["PIP_USER"] = "0"
    if offline is True:
        e["HF_HUB_OFFLINE"] = "1"
    elif offline is False:
        e.pop("HF_HUB_OFFLINE", None)
        e.pop("TRANSFORMERS_OFFLINE", None)
        e["HF_HUB_DISABLE_TELEMETRY"] = "1"
        e["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    return e


_TIMED_OUT = "timed out"   # _probe: the helper did not answer in time (a busy machine), never read as "broken"


def _probe(py, env):
    """The environment's own missing() verdict ({"missing": [...], "version": ..., "pip": bool}); None when its
    interpreter does not run (a broken environment: e.g. the Python it was made from was removed); _TIMED_OUT when
    it did not answer within PROBE_TIMEOUT_S."""
    rc, out = _capture("probe", [str(py), str(HERE / "deps_bootstrap.py"), "--probe"],
                       env=_tool_env(env, offline=True), timeout_s=PROBE_TIMEOUT_S)
    if rc == -1:
        return _TIMED_OUT
    return _last_json(out) if rc == 0 else None


# check: is the model complete in the cache (offline)? download: fetch what is missing, then the same check (exit 3
# when files are still missing — offline, huggingface_hub hands back the cached snapshot as it is). Complete means
# every file in `files` plus each module's config.json that modules.json lists (Normalize has none).
_MODEL_SRC = r"""
import json, os, sys
from huggingface_hub import snapshot_download
mode, model, ignore, files = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), json.loads(sys.argv[4])
try:
    path = snapshot_download(model, ignore_patterns=ignore, local_files_only=(mode == "check"))
except Exception as exc:
    if mode != "check":
        raise
    print(json.dumps({"cached": False, "why": type(exc).__name__}))
    raise SystemExit(0)
need = list(files)
try:
    with open(os.path.join(path, "modules.json"), encoding="utf-8") as f:
        for m in json.load(f):
            p, t = str(m.get("path") or ""), str(m.get("type") or "")
            if p and not t.endswith("Normalize"):
                need.append(p + "/config.json")
except FileNotFoundError:
    pass
except Exception:
    need.append("modules.json (unreadable)")
gone = [f for f in need if not os.path.isfile(os.path.join(path, f))]
print(json.dumps({"cached": not gone, "path": path, "missing": gone}))
raise SystemExit(3 if gone and mode != "check" else 0)
"""


def _model_cmd(py, mode) -> list:
    """argv of the model helper run by `py`: mode "check" (offline) or "download" (fetches only missing files)."""
    return [str(py), "-c", _MODEL_SRC, mode, _model_name(), json.dumps(list(MODEL_IGNORE)),
            json.dumps(list(MODEL_KEY_FILES))]


def _model_cached(py, env) -> bool:
    rc, out = _capture("model-cached", _model_cmd(py, "check"), env=_tool_env(env, offline=True),
                       timeout_s=PROBE_TIMEOUT_S)
    data = _last_json(out) if rc == 0 else None
    return bool(data and data.get("cached") is True)


def _last_json(text):
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                return data if isinstance(data, dict) else None
            except ValueError:
                continue
    return None


def venv_path_problem(vdir, *, windows=None, long_paths=True):
    """Why pip could not write the environment's files at `vdir` (Windows without long-path support: a DEEPMAP_HOME
    too long for the packages' deepest files under its 260-character MAX_PATH), or None."""
    win = (os.name == "nt") if windows is None else bool(windows)
    if not win or long_paths:
        return None
    need = len(str(vdir)) + len("\\Lib\\site-packages\\") + WIN_VENV_LONGEST
    if need < WIN_MAX_PATH:
        return None
    return (f"DeepMap's environment at {vdir} ({len(str(vdir))} characters) is too deep for Windows' "
            f"{WIN_MAX_PATH}-character MAX_PATH: its packages need {need}-character paths. Set DEEPMAP_HOME to a short "
            "path (e.g. C:\\dm) or enable Win32 long paths (LongPathsEnabled=1), then re-run /deep-map:setup.")


def _nearest_existing(path) -> Path:
    p = Path(os.path.abspath(str(path)))
    while not p.exists() and p.parent != p:
        p = p.parent
    return p


def _disk_problems(needs) -> list:
    """[message] for each volume that lacks the room `needs` ([(path, GB, what)]) adds up to on it."""
    groups: dict = {}
    for path, gb, what in needs:
        anchor = _nearest_existing(path)
        try:
            dev = os.stat(str(anchor)).st_dev
        except OSError:
            dev = str(anchor)
        g = groups.setdefault(dev, {"path": anchor, "gb": 0.0, "parts": []})
        g["gb"] += gb
        g["parts"].append(f"{what} about {gb:g} GB")
    out = []
    for g in groups.values():
        try:
            free = _disk_free_bytes(g["path"]) / (1024 ** 3)
        except OSError:
            continue
        if free < g["gb"]:
            out.append(f"only {free:.1f} GB free at {g['path']} — DeepMap's first setup needs about {g['gb']:g} GB "
                       f"there ({', '.join(g['parts'])}). Free some space, or point DEEPMAP_HOME / HF_HOME at a disk "
                       "with room, then re-run /deep-map:setup.")
    return out


def _setup_cmd(script, target) -> str:
    return (f'"{Path(sys.executable).as_posix()}" "{Path(str(script or HERE / "provision.py")).as_posix()}" setup '
            f'"{Path(str(target)).as_posix()}"')


def _log_tail(log_file, lines: int = 15) -> list:
    try:
        text = Path(log_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("=====")][-lines:]


class _Fail(Exception):
    """A bootstrap failure; its message is the verdict's reason, printed once by ensure_env."""


def ensure_env(checks, *, target=".", env=None, script=None, here_missing=None):
    """Build or repair the managed environment and return its interpreter, or None after printing why (the last line is
    "setup verdict: FAILED — ..."). Order: setup's census, then — before anything is installed or downloaded (only
    the build lock exists by then) — the platform when torch is to be installed (an Intel Mac / Rosetta Python: no
    PyTorch build), Java, the Windows path length and free disk; then the venv, pip (pip, torch, the
    packages, the ranged specs), the model and the self-check. A complete environment is reused as is; a broken one is
    re-created only when DeepMap made it and no process runs from it — on macOS so is one that needs work and whose
    Python runs as x86_64 or as another architecture than this one (_env_arch_problem), never repaired in place."""
    checks = checks or SetupChecks()
    e = dict(os.environ if env is None else env)
    t0 = time.monotonic()
    vdir = venv_dir(e)
    log_file = log_path(e)
    retry = (f"re-run /deep-map:setup — it keeps what is already installed (in a terminal: "
             f"{_setup_cmd(script, target)})")

    if checks.census:
        verdict = checks.census(target)
        if verdict:
            _say(verdict, err=True)
            return None
    ver = _python_version()
    if tuple(ver[:2]) < MIN_PYTHON:
        _say(f"setup verdict: FAILED — DeepMap needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer; this Python "
             f"({sys.executable}) is {'.'.join(map(str, ver))}. Install Python 3.10+ (as `python` or `python3`), then "
             "re-run /deep-map:setup.", err=True)
        return None
    raw_home = e.get("DEEPMAP_HOME")
    if raw_home is not None and not str(raw_home).strip():
        # provision reads an empty value as the current directory: the environment would land in the project's venv/
        _say(f"setup verdict: FAILED — DEEPMAP_HOME is set but empty, so DeepMap's environment would be made in the "
             f"current directory ({vdir}). Unset DEEPMAP_HOME (the default is ~/.deepmap) or set it to a directory, "
             "then re-run /deep-map:setup. Nothing was created.", err=True)
        return None

    lock = None
    if checks.file_lock:
        lock = _take_lock(checks, vdir.parent / "run" / "venv-setup.lock")
        if lock is False:
            _say("setup verdict: FAILED — another DeepMap setup has been building DeepMap's environment for "
                 f"{_dur(LOCK_WAIT_S)}; let it finish (or stop it), then re-run /deep-map:setup.", err=True)
            return None
    try:
        return _ensure_locked(checks, e, vdir, log_file, retry, t0, here_missing)
    except _Fail as exc:
        _say(f"setup verdict: FAILED — DeepMap's own Python environment could not be installed: {exc}", err=True)
        return None
    finally:
        if lock and checks.file_unlock:
            try:
                checks.file_unlock(lock)
            except Exception:
                pass


def _take_lock(checks, path):
    """The OS lock serializing environment builds (provision's _file_lock): a handle, None (no lock file possible:
    proceed, best-effort) or False (another setup held it past LOCK_WAIT_S)."""
    handle, how = checks.file_lock(path, 0)
    if how in ("ok", "waited"):
        return handle
    if how != "timeout":
        return None
    _say("another DeepMap setup is building DeepMap's environment right now — waiting for it ...")
    t0 = time.monotonic()
    while time.monotonic() - t0 < LOCK_WAIT_S:
        handle, how = checks.file_lock(path, HEARTBEAT_S)
        if how in ("ok", "waited"):
            return handle
        if how != "timeout":
            return None
        _say(f"      ... still waiting ({_dur(time.monotonic() - t0)} so far)")
    return False


def _inspect(vdir, env) -> dict:
    """{"state": absent | empty | foreign | venv, "marker", "probe"}. A directory is DeepMap's only when it holds
    DeepMap's marker (written before `-m venv` runs, so a creation that died half-way still counts); any other
    non-empty directory — a working virtual environment included — is foreign. probe: _probe's answer, None when
    the venv's interpreter does not run."""
    if not vdir.exists():
        return {"state": "absent"}
    if not vdir.is_dir():
        return {"state": "foreign"}
    if not (vdir / MARKER_NAME).is_file():
        try:
            empty = not any(vdir.iterdir())
        except OSError:
            empty = False
        return {"state": "empty"} if empty else {"state": "foreign", "venv": (vdir / "pyvenv.cfg").is_file()}
    marker = _read_marker(vdir) or {}
    if not (vdir / "pyvenv.cfg").is_file():
        return {"state": "venv", "marker": marker, "probe": None}
    py = venv_python(vdir)
    return {"state": "venv", "marker": marker, "probe": _probe(py, env) if py.is_file() else None}


def _move_aside(vdir) -> Path:
    """Rename `vdir` to a unique sibling; OSError when it can't be renamed (permissions, security software, a file held
    open without delete sharing). Never cleared in place: `-m venv --clear` deletes what it can and stops at the first
    file a running process holds (Windows), leaving the environment every project's servers use without its Python or
    its packages."""
    old = Path(vdir).with_name(f"{Path(vdir).name}.old-{os.getpid()}-{time.time_ns()}")
    os.rename(str(vdir), str(old))
    return old


def _dict(x) -> dict:
    return x if isinstance(x, dict) else {}


def _minor(version):
    """'3.12' from '3.12.10'; None when `version` is not a version."""
    m = re.match(r"\s*(\d+)\.(\d+)", str(version or ""))
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _python_moved(marker, now):
    """(was, now) as 'X.Y' when the environment's Python now runs another minor version than the one its marker says
    it was finished with (a base Python upgraded in place): its packages' compiled parts were built for the old one,
    and on Windows (Lib\\site-packages carries no version) every package still looks installed. None when they agree
    or either is unknown."""
    was, cur = _minor(_dict(marker).get("python_version")), _minor(now)
    return (was, cur) if was and cur and was != cur else None


def _env_arch_problem(probe):
    """macOS: why DeepMap's existing environment must be made afresh rather than repaired in place — its Python, as
    setup starts it, runs as x86_64 (an Intel build, or under Rosetta 2: what an earlier setup under an x86_64 Python
    left), for which PyTorch publishes no current build, or as another architecture than the Python running setup.
    pip runs in that interpreter, so a repair would fail at torch again. None on every other OS, and when the probe
    names no architecture. Never raises."""
    try:
        if _platform() != "darwin":
            return None
        p = _dict(probe)
        env_arch = str(p.get("machine") or "").lower()
        if not env_arch:
            return None
        if env_arch in _X86_64_NAMES:
            how = " under Rosetta 2" if p.get("translated") == 1 else ""
            return (f"its Python runs as x86_64{how}, and PyTorch, which DeepMap's search model needs, publishes no "
                    "current build for x86_64 macOS")
        ours = str(_mac_machine() or "").lower()
        if ours and env_arch != ours:
            return f"its Python runs as {env_arch}, while this Python ({sys.executable}) runs as {ours}"
        return None
    except Exception:
        return None


def _broken_why(probe, moved, arch=None) -> str:
    if arch:
        return arch
    if probe is None:
        return "its Python does not run"
    if moved:
        was, now = moved
        return f"its Python changed from {was} to {now} under it, and its packages were installed for {was}"
    return "it has no pip"


def _ensure_locked(checks, e, vdir, log_file, retry, t0, here_missing):
    st = _inspect(vdir, e)
    py = venv_python(vdir)
    if st["state"] == "foreign":
        # a delete of the environment while DeepMap's servers run from it leaves their loaded files and nothing else
        users = _processes_using(vdir) if vdir.is_dir() else None
        if users and _deepmap_users(users, e):
            who, how = _users_and_stop(users, e)
            raise _Fail(f"{vdir} is not a DeepMap environment (it has no {MARKER_NAME}), so DeepMap does not touch it; "
                        f"{len(users)} process(es) still run from it: {who}. If it is what a delete of DeepMap's "
                        "environment left while DeepMap's servers ran from it (the files they hold open stay): "
                        f"{how}, then delete {vdir} and re-run /deep-map:setup. If it is not DeepMap's, set "
                        "DEEPMAP_HOME to another directory, then re-run /deep-map:setup.")
        running = f"; {len(users)} process(es) run from it: {_users_and_stop(users, e)[0]}" if users else ""
        if st.get("venv"):
            raise _Fail(f"{vdir} holds a virtual environment DeepMap did not make (it has no {MARKER_NAME}){running}; "
                        "DeepMap never installs into it — move it aside, or set DEEPMAP_HOME to another directory, "
                        "then re-run /deep-map:setup.")
        raise _Fail(f"{vdir} exists but is not a DeepMap environment (it has no {MARKER_NAME}){running} — move it "
                    "aside, or set DEEPMAP_HOME elsewhere, then re-run /deep-map:setup.")
    probe = st.get("probe")
    if probe is _TIMED_OUT:
        raise _Fail(f"DeepMap's environment ({py}) did not answer within {PROBE_TIMEOUT_S // 60} min — the machine "
                    f"may be very busy. It was left as it is; {retry}.")
    marker = _dict(st.get("marker"))
    create = st["state"] in ("absent", "empty")
    moved = _python_moved(marker, _dict(probe).get("version")) if st["state"] == "venv" else None
    # broken: its Python does not run, it moved to another minor version under it, or it has no pip (Debian's
    # `-m venv` without python3-venv leaves exactly that)
    rebuild = st["state"] == "venv" and (probe is None or moved is not None or _dict(probe).get("pip") is False)
    probe_missing = [] if (create or rebuild) else list(_dict(probe).get("missing") or [])
    install = create or rebuild or bool(probe_missing)
    was_complete = marker.get("complete") is True
    complete = was_complete and not marker.get("repair_pending") and not install
    # macOS: an environment that is not reused as is and whose Python runs as x86_64 (or as another architecture than
    # this one) is made afresh — a repair would run pip, and torch, in that interpreter again. When this Python cannot
    # serve either, the platform check refuses before anything is moved (_prereqs runs before _replace).
    arch = _env_arch_problem(probe) if st["state"] == "venv" and not rebuild and not complete else None
    if arch:
        rebuild, probe_missing, install = True, [], True
    keep_usable = was_complete and not (create or rebuild)   # the commands use it: a failed setup never takes it away
    torch_needed = create or rebuild or any(_canon(m.get("name")) == "torch" for m in probe_missing)

    cached = None if install else _model_cached(py, e)
    model_needed = (not cached) if cached is not None else not _model_cached_guess(e)
    if complete and cached:
        what = f" ({_describe(here_missing)})" if here_missing else ""
        _say(f"this Python ({sys.executable}) lacks DeepMap's packages{what} — using DeepMap's own environment ({py})")
        return str(py)

    if create:
        _say(f"DeepMap's Python packages are missing from this Python ({sys.executable}): {_describe(here_missing)}.")
        _say(f"Setup installs them into DeepMap's own environment, {vdir}, and runs from there — your Python is not "
             "changed. This happens once per machine: about 1 GB of downloads and some minutes.")
    elif rebuild:
        _say(f"DeepMap's own environment ({vdir}) must be re-created: {_broken_why(probe, moved, arch)}.")
    elif install:
        _say(f"repairing DeepMap's own environment ({vdir}): {_describe(probe_missing)}")
    elif model_needed:
        _say(f"DeepMap's environment ({vdir}) is installed; the embedding model is not complete in the local cache.")
    else:
        _say(f"finishing DeepMap's environment ({vdir}): the embedding model check and the self-check")

    checked = False
    if install or model_needed:
        _prereqs(checks, e, vdir, install=install, model=model_needed, torch=install and torch_needed)
        checked = True
    if rebuild:   # before the log is opened: a refusal changes nothing, the last build's log included
        # a bare python still hands commands to an environment whose files look intact: the stop command then
        # carries the opt-out, so it runs with that bare python
        _replace(vdir, e, optout=managed_python(e) is not None and (probe is None or moved is not None),
                 why=_broken_why(probe, moved, arch))

    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "wb") as log:
        _say(f"details: {log_file}")
        tenv = _tool_env(e)
        plan = (["venv"] if (create or rebuild) else []) + (
            [k for k, _ in install_commands(py, requirements(), torch=torch_needed)] if install else []) + [
            "model", "selfcheck"]
        n = [0, len(plan)]

        def step(key, argv, what, timeout_s, env):
            if key not in plan:
                plan.append(key)
                n[1] = len(plan)
            n[0] += 1
            _say(f"[{n[0]}/{n[1]}] {what} ...")
            rc, secs = _run_step(key, argv, env=env, log=log, what=what, timeout_s=timeout_s)
            if rc == 0:
                _say(f"      done ({_dur(secs)})")
            return rc

        try:
            if create or rebuild:
                vdir.mkdir(parents=True, exist_ok=True)
                _write_marker(vdir, complete=False)   # DeepMap's from the start: a killed creation is re-made
                rc = step("venv", [sys.executable, "-m", "venv", str(vdir)],
                          "creating DeepMap's virtual environment" + (" again" if rebuild else ""), VENV_TIMEOUT_S,
                          tenv)
                if rc != 0:
                    raise _Fail(_venv_failure(rc, log_file, retry))
            if install:
                if keep_usable:   # a repair: the commands keep using it meanwhile; the next setup retries a failure
                    _write_marker(vdir, complete=True, pending=True, python_version=marker.get("python_version"),
                                  finished_against=marker.get("requirements_sha256"))
                else:
                    _set_incomplete(vdir)
                labels = {"pip": "updating pip", "torch": "installing PyTorch (the CPU build)",
                          "packages": "installing sentence-transformers, transformers and the other unpinned packages",
                          "pinned": "installing the version-pinned packages (neo4j, mcp, graphifyy, ...)"}
                for key, argv in install_commands(py, requirements(), torch=torch_needed):
                    rc = step(key, argv, labels.get(key, key), PIP_TIMEOUT_S, tenv)
                    if rc != 0:
                        raise _Fail(_pip_failure(labels.get(key, key), rc, log_file, retry, vdir, e))
                after = _probe(py, e)
                if after is _TIMED_OUT:
                    raise _Fail(f"after the install DeepMap's environment ({py}) did not answer within "
                                f"{PROBE_TIMEOUT_S // 60} min — the machine may be very busy. Details: {log_file}. "
                                f"Then {retry}.")
                if after is not None:
                    st["probe"] = after
                still = list(after.get("missing") or []) if isinstance(after, dict) else None
                if still is None or still:
                    raise _Fail(("after the install its Python does not run" if still is None else
                                 "after the install these are still missing or out of range: " + _describe(still))
                                + f". Details: {log_file}. Then {retry}.")
            if cached is None:
                cached = _model_cached(py, e)
            if not cached and not checked:
                _prereqs(checks, e, vdir, install=False, model=True)
            # Always, before the self-check: it fetches only the files the cache lacks (one listing when it is
            # complete), so a model cut off half-way is finished here instead of failing every self-check.
            model = _model_name()
            what = (f"checking the embedding model {model} against huggingface.co (missing files only)" if cached
                    else f"downloading the embedding model {model} (about 0.6 GB)")
            rc = step("model", _model_cmd(py, "download"), what, MODEL_TIMEOUT_S, _tool_env(e, offline=False))
            if rc == 3:
                raise _Fail(_model_incomplete(log_file, retry))
            if rc != 0 and not cached:
                raise _Fail(_step_failure(f"downloading the embedding model {model} from huggingface.co", rc,
                                          log_file, retry, network=True, timeout_s=MODEL_TIMEOUT_S))
            if rc != 0:
                _say(f"      huggingface.co could not be reached (exit {rc}); the cached copy looks complete, so the "
                     "self-check decides")
            rc = step("selfcheck", [str(py), str(SELFCHECK)],
                      "self-check: imports, pinned ranges, graphify, a 768-dimension model probe (offline)",
                      SELFCHECK_TIMEOUT_S, _tool_env(e, offline=True))
            if rc != 0:
                raise _Fail(_selfcheck_failure(rc, log_file, retry, e))
        except _Fail:
            if keep_usable:
                _settle_failed(py, e, vdir, marker, probe_missing)
            raise
    _write_marker(vdir, complete=True, python_version=_dict(st.get("probe")).get("version"))
    _say(f"DeepMap's environment is ready ({_dur(time.monotonic() - t0)}): {py}")
    return str(py)


def _process_table():
    """[(pid, command line)] of this machine's processes: one CIM query on Windows, /proc on Linux, else `ps`. None
    when the OS could not be asked, or answered nothing (this process itself is always there). Never raises."""
    rows: list = []
    try:
        if os.name == "nt":
            ps = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-CimInstance Win32_Process | "
                  "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=PROBE_TIMEOUT_S)
            if r.returncode != 0:
                return None
            rows = [ln.split("\t", 1) for ln in r.stdout.decode("utf-8", "replace").splitlines() if "\t" in ln]
        elif os.path.isdir("/proc"):
            for d in os.listdir("/proc"):
                if d.isdigit():
                    try:
                        with open(f"/proc/{d}/cmdline", "rb") as fh:
                            rows.append((d, fh.read().replace(b"\0", b" ").decode("utf-8", "replace")))
                    except OSError:
                        continue
        else:
            r = subprocess.run(["ps", "-axww", "-o", "pid=,command="], capture_output=True, stdin=subprocess.DEVNULL,
                               timeout=PROBE_TIMEOUT_S)
            if r.returncode != 0:
                return None
            rows = [ln.strip().split(None, 1) for ln in r.stdout.decode("utf-8", "replace").splitlines()]
    except Exception:
        return None
    return rows or None


def _processes_using(vdir):
    """[(pid, command line)] of the live processes started from the environment at `vdir` (their command line names a
    file inside it: its interpreter), this process left out; None when the process table cannot be read — never taken
    for "none". Never raises."""
    rows = _process_table()
    if rows is None:
        return None
    want = os.path.normcase(os.path.abspath(str(vdir))).rstrip("\\/") + os.sep
    out = []
    for row in rows:
        if len(row) != 2 or not str(row[0]).strip().isdigit() or int(row[0]) == os.getpid():
            continue
        if want in os.path.normcase(str(row[1])):
            out.append((int(row[0]), str(row[1]).strip()))
    return out


def _owner_records(home) -> dict:
    """{pid: owner record} from the MCP servers' owner records (provision's <DEEPMAP_HOME>/run/<name>-<port>.owner)."""
    recs: dict = {}
    try:
        for f in (Path(home) / "run").glob("*.owner"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(rec, dict) and rec.get("root"):
                    recs[int(rec.get("pid"))] = rec
            except Exception:
                continue
    except Exception:
        pass
    return recs


_SERVER_SCRIPTS = ("deepmap_search_server.py", "deepmap_embed_server.py")


def _server_roots(users, env) -> dict:
    """{pid: project root} for those of `users` that are DeepMap's MCP servers: an owner record names the pid AND the
    process still runs that record's server script (provision's own test). A pid alone proves nothing: only mcp-stop
    removes a record, so after a reboot or a crash its pid can be another program's."""
    recs = _owner_records(home_dir(env))
    roots: dict = {}
    for pid, cmd in users:
        rec = recs.get(pid)
        if not rec:
            continue
        script = os.path.basename(str(rec.get("script") or "").replace("\\", "/"))
        names = (script,) if script else _SERVER_SCRIPTS
        if any(n and n.lower() in str(cmd).lower() for n in names):
            roots[pid] = str(rec["root"])
    return roots


_SCRIPT = re.compile(r"[\w.-]+\.py\b")
# DeepMap's entry scripts as its processes run them: <plugin>/build/<script>.py
_DEEPMAP_SCRIPT = re.compile(r"\bbuild[\\/](?:deepmap_search_server|deepmap_embed_server|deepmap_cli|provision|"
                             r"deps_bootstrap)\.py\b", re.I)
# what sits beside such a script in a DeepMap build directory (every DeepMap version ships both)
_BUILD_SIGNATURE = ("deepmap_cli.py", "deepmap_search_server.py")


def _deepmap_script(cmd):
    """The DeepMap entry script a command line runs, as written there, or None: a <dir>/build/<script>.py whose
    directory also holds DeepMap's other entry points (another program's build/provision.py does not). A command line
    does not mark where a path starts (quoted or not: /proc joins argv with spaces), so each start a quote or a space
    allows is tried; only an absolute path counts (a relative one is the other process's)."""
    cmd = str(cmd)
    for m in _DEEPMAP_SCRIPT.finditer(cmd):
        head = cmd[:m.end()]
        starts = [j + 1 for j in range(m.start() - 1, -1, -1) if head[j] in " \t\"'"] + [0]
        for b in starts:
            path = head[b:].strip().strip("\"'")
            if os.path.isabs(path):
                d = os.path.dirname(path)
                if all(os.path.isfile(os.path.join(d, f)) for f in _BUILD_SIGNATURE):
                    return path
    return None


def _deepmap_users(users, env) -> bool:
    """Is any of `users` DeepMap's own: one of its servers (an owner record whose pid runs that server's script), or a
    process running one of its entry scripts from a DeepMap build directory (a sync's engine runs beside its sync)?"""
    roots = _server_roots(users, env)
    return any(pid in roots or _deepmap_script(cmd) for pid, cmd in users)


def _script_label(cmd) -> str:
    """The script a command line runs: DeepMap's entry script when it is one, else its first *.py."""
    m = _DEEPMAP_SCRIPT.search(str(cmd))
    if m:
        return re.split(r"[\\/]", m.group(0))[-1]
    found = _SCRIPT.findall(str(cmd))
    return found[0] if found else ""


def _users_and_stop(users, env, *, optout: bool = False) -> tuple:
    """(who, how): the processes running from the environment (pid, script, a server's project from its owner
    record) and how to stop them — `provision.py mcp-stop "<project>"` per project, else in each project. optout: a
    bare python would still hand this command to DeepMap's environment, which cannot run it (its Python does not start,
    or its packages do not load), so the command sets DEEPMAP_MANAGED_VENV=0 and runs with this Python."""
    roots = _server_roots(users, env)
    shown = []
    for pid, cmd in users[:5]:
        label = _script_label(cmd)
        bits = [label] if label else []
        bits += [f"project {roots[pid]}"] if pid in roots else []
        shown.append(f"pid {pid}" + (f" ({', '.join(bits)})" if bits else ""))
    more = f" and {len(users) - 5} more" if len(users) > 5 else ""
    prov = (f"{OPTOUT_ENV}=0 " if optout else "") + (
        f'"{Path(sys.executable).as_posix()}" "{Path(HERE / "provision.py").as_posix()}" mcp-stop')
    projects = sorted({roots[pid] for pid, _ in users if pid in roots})
    stops = (", ".join(f'`{prov} "{r}"`' for r in projects) if projects
             else f"`{prov}` in each project that uses DeepMap")
    why = f"; {OPTOUT_ENV}=0 runs it with this Python, not in DeepMap's broken environment" if optout else ""
    return (f"{'; '.join(shown)}{more}" if shown else "",
            f"Stop DeepMap's servers first ({stops}{why}; closing Claude Code does not stop them) and let any other "
            "DeepMap command finish")


_NOT_WORKING = "its Python or pip does not work"


def _in_use(vdir, users, env, *, optout: bool = False, why: str = _NOT_WORKING) -> str:
    """The FAILED reason for an environment that must be re-created while processes run from it."""
    who, how = _users_and_stop(users, env, optout=optout)
    return (f"{vdir} must be re-created ({why}), but {len(users)} process(es) still run from it: {who}. {how}, then "
            "re-run /deep-map:setup. Nothing was changed.")


def _replace(vdir, env, *, optout: bool = False, why: str = _NOT_WORKING) -> None:
    """Move a broken environment out of the way (it is made afresh), then delete the moved copy — only when the process
    table shows nothing running from it. Nothing else stops the move: Windows renames the directory under a running
    server too, and the delete then removes all but its loaded files (measured 2026-10-05). So a table that cannot be
    read is a refusal as well. optout: see _users_and_stop; why: what is wrong with it."""
    users = _processes_using(vdir)
    if users is None:
        _who, how = _users_and_stop([], env, optout=optout)
        raise _Fail(f"{vdir} must be re-created ({why}), but this machine's process list could not be read to make "
                    f"sure no DeepMap process runs from it. {how}; or, once nothing runs from it, delete {vdir} "
                    "yourself. Then re-run /deep-map:setup. Nothing was changed.")
    if users:
        raise _Fail(_in_use(vdir, users, env, optout=optout, why=why))
    try:
        old = _move_aside(vdir)
    except OSError as exc:
        _who, how = _users_and_stop([], env, optout=optout)
        raise _Fail(f"{vdir} must be re-created ({why}), but it could not be moved aside ({exc}): a process may still "
                    f"be running from it. {how}, then re-run /deep-map:setup. Nothing was changed.")
    shutil.rmtree(str(old), ignore_errors=True)
    if old.exists():
        _say(f"NOTE: part of the old environment could not be removed ({old}); delete that directory once "
             "DeepMap's servers are stopped.")


def _settle_failed(py, e, vdir, marker, before) -> None:
    """A setup failed on an environment the commands were using: keep it in use (complete, repair pending: the next
    setup tries again) when a re-probe finds it no worse than before — pip rolls a failed install back — and mark
    it incomplete when its Python no longer runs or a package it had is now absent."""
    after = _probe(py, e)
    if after is _TIMED_OUT:
        worse = False   # no answer in time: it answered a moment ago, keep it
    elif not isinstance(after, dict):
        worse = True
    else:
        had = {_canon(m.get("name")): m.get("found") for m in before or []}
        worse = any(_canon(m.get("name")) not in had or (m.get("found") is None and had[_canon(m.get("name"))])
                    for m in after.get("missing") or [])
    try:
        _write_marker(vdir, complete=not worse, pending=not worse, python_version=marker.get("python_version"),
                      finished_against=marker.get("requirements_sha256"))
    except OSError:
        return
    if not worse:
        _say("DeepMap's environment is as usable as before this setup, so the commands keep using it; the next "
             "/deep-map:setup tries again.")


def _prereqs(checks, e, vdir, *, install: bool, model: bool, torch: bool = False) -> None:
    """The platform (when torch is to be installed: PyTorch must publish a build for it), Java (preflight's rule), the
    Windows path length and free disk — before anything is installed or downloaded."""
    plat = _platform_problem() if torch else None
    if plat:
        _say(f"PREFLIGHT FAILED: {plat[0]}", err=True)
        raise _Fail(f"{plat[1]}. Nothing was downloaded.")
    problems = list(checks.java_problems() or []) if checks.java_problems else []
    for msg, _short in problems:
        _say(f"PREFLIGHT FAILED: {msg}", err=True)
    if problems:
        raise _Fail(f"preflight failed ({'; '.join(s for _, s in problems)}) — Neo4j needs Java 17 or 21. Nothing "
                    "was downloaded. Fix it, then re-run /deep-map:setup.")
    if install and checks.long_paths_enabled is not None:
        prob = venv_path_problem(vdir, long_paths=bool(checks.long_paths_enabled()))
        if prob:
            _say(f"PREFLIGHT FAILED: {prob}", err=True)
            raise _Fail("DEEPMAP_HOME is too long for Windows' MAX_PATH (see above). Nothing was downloaded.")
    needs = []
    if install:
        needs.append((vdir.parent, VENV_GB, "Python packages"))
    if model:
        needs.append((_hf_hub_cache(e), MODEL_GB, "the embedding model"))
    if checks.neo4j_download_needed and checks.neo4j_download_needed():
        needs.append((vdir.parent, NEO4J_GB, "Neo4j"))
    problems = _disk_problems(needs)
    for msg in problems:
        _say(f"PREFLIGHT FAILED: {msg}", err=True)
    if problems:
        raise _Fail("not enough free disk (see above). Nothing was downloaded.")


def _show_tail(log_file) -> None:
    tail = _log_tail(log_file)
    if tail:
        _say(f"last lines of {log_file}:", err=True)
        for line in tail:
            _say(f"  | {line}", err=True)


def _platform() -> str:
    return sys.platform


def _mac_machine() -> str:
    """This Python's architecture on a real Mac ("arm64" / "x86_64"; Rosetta 2 reports "x86_64"), "" on any other OS
    — never the host's architecture under a simulated darwin (_platform)."""
    if sys.platform != "darwin":
        return ""
    import platform
    return platform.machine() or ""


def _sysctl_ctypes(name: str):
    """macOS: the integer sysctl `name` through libc's sysctlbyname (no process is started), or None (no such key,
    or ctypes / the symbol unavailable). Never raises."""
    try:
        import ctypes
        fn = ctypes.CDLL(None).sysctlbyname
        fn.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p,
                       ctypes.c_size_t]
        fn.restype = ctypes.c_int
        val = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(val))
        if fn(name.encode("ascii"), ctypes.byref(val), ctypes.byref(size), None, 0) != 0:
            return None
        return int(val.value)
    except Exception:
        return None


def _sysctl_int(name: str):
    """macOS: the integer sysctl `name` (sysctlbyname, else `sysctl -n` with a 5 s timeout), or None: another OS
    (nothing is run), no such key (sysctl.proc_translated does not exist on an Intel Mac), or no answer. Never raises."""
    if _platform() != "darwin":
        return None
    val = _sysctl_ctypes(name)
    if val is not None:
        return val
    try:
        p = subprocess.run(["/usr/sbin/sysctl", "-n", name], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=5)
        out = (p.stdout or "").strip()
        return int(out) if p.returncode == 0 and re.fullmatch(r"-?\d+", out) else None
    except Exception:
        return None


_X86_64_NAMES = ("x86_64", "amd64", "i386")


def _platform_problem(plat=None, machine=None, translated=None):
    """(message, short form) when PyTorch publishes no current build for this Python's platform, else None. That is
    macOS on x86_64: no torch wheel for it after 2.2.2 (cp310-cp312 only), and the transformers release a fresh setup
    installs needs a newer torch, so setup would download about 1 GB and then fail at the torch step or the
    self-check. An Intel Mac is not supported; an x86_64 Python under Rosetta 2 on Apple silicon
    (sysctl.proc_translated = 1) is told to give way to a native arm64 one. Apple-silicon arm64 and every other OS:
    None. Arguments left None are read from this machine (_platform, _mac_machine, _sysctl_int). Never raises."""
    try:
        plat = _platform() if plat is None else plat
        if plat != "darwin":
            return None
        arch = str(_mac_machine() if machine is None else machine).lower()
        if arch not in _X86_64_NAMES:
            return None
        rosetta = (_sysctl_int("sysctl.proc_translated") == 1) if translated is None else bool(translated)
    except Exception:
        return None
    if rosetta:
        # /deep-map:setup runs `python`, and `python3` only where there is no `python` (commands/setup.md); no setting
        # picks the interpreter that makes this decision (the heal's and the git hook's interpreter setting does not)
        return (f"this Python ({sys.executable}) is an x86_64 build running under Rosetta 2 on an Apple-silicon Mac, "
                "and PyTorch, which DeepMap's search model needs, publishes no current build for x86_64 macOS. "
                "/deep-map:setup runs the `python` on the PATH Claude Code runs commands with, or python3 where there "
                "is none: make that a native arm64 Python 3.10+, then re-run /deep-map:setup. Homebrew's is one "
                "(/opt/homebrew/bin/python3, and as `python` from $(brew --prefix python)/libexec/bin; arm64 only, so "
                "it never runs under Rosetta); an Intel Homebrew's /usr/local/bin/python3 runs under it, and so does a "
                "universal Python started from an x86_64 app. `python -c \"import platform; "
                "print(platform.machine())\"` prints arm64 for a native one.",
                "this Python runs as x86_64 under Rosetta 2, and PyTorch has no current build for x86_64 macOS — use a "
                "native arm64 Python (see above)")
    return (f"this is an Intel Mac (this Python, {sys.executable}, runs as {arch}): DeepMap's search model needs "
            "PyTorch, and PyTorch publishes no current build for Intel Macs — its last one, 2.2.2, is older than the "
            "transformers release setup installs needs. Intel Macs are not supported; DeepMap runs on Apple-silicon "
            "Macs, Linux and Windows.",
            "Intel Macs are not supported (PyTorch has no current build for macOS x86_64)")


def _ensurepip_missing() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("ensurepip") is None
    except Exception:
        return False


def _venv_failure(rc, log_file, retry) -> str:
    """Why `-m venv` failed. Debian / Ubuntu's missing python3-venv only on Linux, from Debian's own wording or a
    Python with no ensurepip module: CPython prints "... '-m', 'ensurepip' ... returned non-zero exit status" for
    every failure at its pip stage, on every OS."""
    _show_tail(log_file)
    text = "\n".join(_log_tail(log_file, 60))
    plat = _platform()
    if plat.startswith("linux") and ("ensurepip is not" in text or "python3-venv" in text or _ensurepip_missing()):
        pkg = f"python3.{sys.version_info[1]}-venv"
        return (f"this Python ({sys.executable}) cannot create a virtual environment: its venv / ensurepip support is "
                f"not installed. On Debian / Ubuntu install it with `sudo apt install {pkg}` (python3-venv on most "
                f"releases), then {retry}.")
    if rc is None:
        return f"`{sys.executable} -m venv` could not be started. Details: {log_file}. Then {retry}."
    if "ensurepip" in text:
        hint = (" — security software (AppLocker, Defender's attack-surface rules) may block the new python.exe it "
                "starts" if plat == "win32" else "")
        return (f"creating the virtual environment failed at its pip step (ensurepip, exit {rc}){hint}; its last "
                f"lines are above. Details: {log_file}. Fix the cause, then {retry}.")
    return f"creating the virtual environment failed (exit {rc}). Details: {log_file}. Fix the cause, then {retry}."


def _step_failure(what, rc, log_file, retry, *, network: bool, timeout_s: int, extra: str = "", hint=None) -> str:
    _show_tail(log_file)
    if rc == -1:
        why = f"{what} did not finish within {timeout_s // 60} min"
    elif rc is None:
        why = f"{what} could not be started"
    else:
        why = f"{what} failed (exit {rc})"
    if hint is None:
        hint = (" — usually the network, a proxy or a package index that cannot be reached; check them" if network
                else "; its FAIL line is above")
    return f"{why}{hint}{extra}. Details: {log_file}. Then {retry}."


# pip's own words when the index could not be reached or refused a download (a "no matching distribution" then
# means nothing about this platform). An index PAGE that answers 401 / 403 / 404 is logged at debug level only:
# pip then prints just "No matching distribution" (measured, pip 26), so that verdict names both causes.
_INDEX_TROUBLE = ("Retrying (Retry(", "NewConnectionError", "ConnectTimeout", "ReadTimeout", "Read timed out",
                  "ProxyError", "SSLError", "Max retries exceeded", "Temporary failure in name resolution",
                  "getaddrinfo failed", "Name or service not known", "nodename nor servname", "Connection refused",
                  "Connection reset", "Network is unreachable", "HTTP error")
_NO_DIST = re.compile(r"No matching distribution found for ([A-Za-z0-9][A-Za-z0-9._-]*)")
# Windows' words for a file another process holds open (a loaded extension module of a running server)
_FILE_IN_USE = ("[WinError 5]", "[WinError 32]", "being used by another process")


def _platform_tag() -> str:
    import platform
    return (f"Python {'.'.join(map(str, sys.version_info[:3]))}, {sys.platform} {platform.machine() or '?'}, "
            f"{64 if sys.maxsize > 2 ** 32 else 32}-bit")


def _last_step_text(log_file) -> str:
    """The log's last step: the lines after its last `===== <time> <what>` header."""
    try:
        lines = Path(log_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    heads = [i for i, ln in enumerate(lines) if ln.startswith("===== ") and not ln.startswith("===== exit")]
    return "\n".join(lines[heads[-1] + 1:] if heads else lines)


def _pip_no_index(vdir, env) -> bool:
    """Is pip set not to use any package index (PIP_NO_INDEX, or no-index in a pip configuration file)? It then prints
    the same "No matching distribution" lines as for a platform with no build. One `pip config list`, on this failure
    path only. Never raises."""
    try:
        if str(env.get("PIP_NO_INDEX", "")).strip().lower() in ("1", "true", "yes", "on"):
            return True
        rc, out = _capture("pip-config", [str(venv_python(vdir)), "-m", "pip", "config", "list"],
                           env=_tool_env(env), timeout_s=PROBE_TIMEOUT_S)
        return rc == 0 and re.search(r"no[-_]index\s*=\s*'?(?:1|true|yes|on)'?\s*$", out or "", re.I | re.M) is not None
    except Exception:
        return False


def _pip_failure(what, rc, log_file, retry, vdir, env) -> str:
    """Why a pip step failed: a file in the environment that a running process holds (Windows: pip cannot replace
    it), pip set not to use any package index, pip finding no release of a package for this Python / platform (its
    "No matching distribution found" with no sign of network or index trouble in that step's log), else the network /
    index hint."""
    text = _last_step_text(log_file)
    if rc not in (None, -1) and any(s in text for s in _FILE_IN_USE):
        who, how = _users_and_stop(_processes_using(vdir) or [], env)
        hint = (" — a file in DeepMap's environment is held by a running process, so pip could not replace it"
                + (f" (running from it: {who})" if who else " (or security software blocked it)") + f". {how}")
        return _step_failure(what, rc, log_file, retry, network=False, timeout_s=PIP_TIMEOUT_S, hint=hint)
    m = _NO_DIST.search(text)
    if rc not in (None, -1) and m and not any(s in text for s in _INDEX_TROUBLE):
        pkg = m.group(1)
        if _pip_no_index(vdir, env):
            hint = (f" — pip is set not to use any package index (PIP_NO_INDEX, or no-index in a pip configuration "
                    f"file), so it found no {pkg} to install: DeepMap's environment needs pypi.org and "
                    "download.pytorch.org (or an index pip may use instead) for this setup")
        elif _canon(pkg) == "torch":
            hint = (f" — pip found no PyTorch CPU build for this Python and platform ({_platform_tag()}) at "
                    f"{TORCH_INDEX} and logged no network error: PyTorch publishes none for some (a 32-bit Python, "
                    "an Intel Mac on a recent Python), and a proxy or firewall that refused pip's request leaves the "
                    "same lines. DeepMap's search model needs PyTorch: use a 64-bit Python version PyTorch publishes a "
                    "CPU build for (that index lists them), or check that this machine can open that index")
        else:
            hint = (f" — pip found no {pkg} release for this Python and platform ({_platform_tag()}) and logged no "
                    f"network error: {pkg} may not support this Python version or platform, or the package index pip "
                    "used (PIP_INDEX_URL) does not carry it or refused pip's request")
        return _step_failure(what, rc, log_file, retry, network=False, timeout_s=PIP_TIMEOUT_S, hint=hint)
    return _step_failure(what, rc, log_file, retry, network=True, timeout_s=PIP_TIMEOUT_S)


def _model_incomplete(log_file, retry) -> str:
    _show_tail(log_file)
    gone = (_last_json("\n".join(_log_tail(log_file, 60))) or {}).get("missing") or []
    return (f"after the download the embedding model is still incomplete in the local Hugging Face cache (missing: "
            f"{', '.join(map(str, gone)) or 'see the log'}) — huggingface.co could not be reached; check the network "
            f"or proxy. Details: {log_file}. Then {retry}.")


def _selfcheck_failure(rc, log_file, retry, env) -> str:
    text = "\n".join(_log_tail(log_file, 60))
    extra = ""
    if "model load/encode failed" in text or "not (fully) in the local Hugging Face cache" in text:
        try:   # the cached files themselves: a re-run would load the very same ones
            extra = (f" — the embedding model in the local cache could not be loaded: delete {_model_cache_dir(env)} "
                     "(setup downloads it again)")
        except Exception:
            pass
    return _step_failure("the environment's self-check", rc, log_file, retry, network=False,
                         timeout_s=SELFCHECK_TIMEOUT_S, extra=extra)


# ---- the remedy every surface prints for a missing package ----------------------------------------------------------
def setup_remedy(names, env=None):
    """The fix to print when some of `names` (requirements.txt spellings) are missing from this interpreter or held
    outside their range: "run /deep-map:setup (...)", which installs them into DeepMap's own environment — never a pip
    command into the user's Python. None when DEEPMAP_MANAGED_VENV=0, or when none of `names` is missing here: then
    the caller's pip command stands (a package that is installed but fails to import is not something setup
    repairs) — except in DeepMap's own environment once its Python moved to another minor version under it: its
    packages were installed for the old one (on Windows still listed, their compiled parts not loading), and setup
    re-creates it. The doctor's driver and ML-stack lines, the sync verdict and graphify's install command all ask
    this. Never raises."""
    try:
        e = os.environ if env is None else env
        if opted_out(e):
            return None
        vdir = venv_dir(e)
        ours = _same_file(sys.executable, venv_python(vdir))   # this Python IS that environment (it predates a change)
        moved = _python_moved(_read_marker(vdir), ".".join(map(str, _python_version()))) if ours else None
        if moved:
            was, now = moved
            return (f"run /deep-map:setup (DeepMap's own environment, {vdir}, was finished with Python {was} and now "
                    f"runs {now}, which cannot use the packages installed for {was}: setup re-creates it)")
        want = {_canon(n) for n in names}
        miss = [m for m in missing() if _canon(m.get("name")) in want]
        if not miss:
            return None
        parts = []
        for m in miss:
            spec = f"{m.get('name')}{_clauses(m.get('spec'))}"
            parts.append(f"{spec} (this Python has {m['found']})" if m.get("found") else spec)
        return (f"run /deep-map:setup (it installs {', '.join(parts)} into DeepMap's own environment, {vdir}"
                + (")" if ours else ", not into this Python)"))
    except Exception:
        return None


# ---- --probe ---------------------------------------------------------------------------------------------------------
def _probe_main() -> int:
    """The environment's answer to _probe, printed by its own interpreter: the packages it lacks, its Python version,
    pip, and the architecture it runs as — machine (platform.machine()) and, on macOS, translated
    (sysctl.proc_translated: 1 under Rosetta 2; None on another OS, where nothing is run for it)."""
    import importlib.util
    import platform
    print(json.dumps({"python": sys.executable, "version": ".".join(map(str, sys.version_info[:3])),
                      "pip": importlib.util.find_spec("pip") is not None, "missing": missing(),
                      "machine": platform.machine() or "", "translated": _sysctl_int("sysctl.proc_translated")}))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--probe"]:
        raise SystemExit(_probe_main())
    print(__doc__)
    raise SystemExit(2)
