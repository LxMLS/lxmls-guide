#!/usr/bin/env python3
"""Build the LxMLS lab guide as a static website section.

Reads the LaTeX sources in this directory and emits HTML pages (one per Day plus
a landing page) into ../lxmls-website/guide/. The output is committed to the
repo so GitHub Pages deploys it as-is; re-run this script whenever the guide
changes.

Pipeline per chapter:
  flatten \\input -> preprocess custom envs / macros / images -> pandoc -> HTML
  -> wrap in web/template.html.

Requires: pandoc, pdftocairo (poppler), inkscape.
"""

import html
import re
import shutil
import subprocess
import sys
from pathlib import Path

GUIDE = Path(__file__).resolve().parent
WEB = GUIDE / "web"
VENDOR = WEB / "vendor"
SITE = GUIDE.parent / "lxmls-website"
OUT = SITE / "guide"
ASSETS = OUT / "assets"
FIGS_OUT = ASSETS / "figs"
BIB = GUIDE / "guide.bib"

# slug, day label, title, source chapter file (the 5 active Days in guide.tex)
CHAPTERS = [
    ("day0", "Day 0", "Basic Tutorials", "pages/intro/intro.tex"),
    ("day1", "Day 1", "Linear Classifiers", "pages/classification/classification.tex"),
    ("day2", "Day 2", "Non-Linear Classifiers", "pages/deep_learning/deep_learning_ff.tex"),
    ("day3", "Day 3", "Sequence Models", "pages/deep_learning/deep_learning_rnn.tex"),
    ("day4", "Day 4", "Transformers", "pages/transformers.tex"),
]

# Theorem-like environments -> CSS class + display name. Numbered per chapter.
BOX_ENVS = {
    "exercise": ("guide-exercise", "Exercise"),
    "theorem": ("guide-theorem", "Theorem"),
    "definition": ("guide-definition", "Definition"),
    "example": ("guide-box", "Example"),
    "remark": ("guide-box", "Remark"),
}

# Editor/margin macros that must not appear in the published guide.
DROP_CMDS = ["afm", "jg", "gka", "todo", "out"]

# Macros pandoc can't parse cleanly; defined directly for MathJax instead.
MACRO_BLACKLIST = {"independent", "independenT"}

# Editor/margin macros: stripped from the text, so don't emit them to MathJax
# either (they carry \textcolor/\bf/\sc which only add noise to the config).
MATHJAX_DROP = {"afm", "jg", "gka", "todo", "out"}

# Overrides/additions for MathJax so non-builtin macros don't render raw.
# (tuples are (body, nargs); these override anything parsed from the sources.)
MATHJAX_EXTRA = {
    "independent": (r"{\perp\!\!\!\perp}", 0),
    "vect": (r"\boldsymbol{#1}", 1),   # avoid \ensuremath/\bm
    "ensuremath": (r"#1", 1),
    "bm": (r"\boldsymbol{#1}", 1),
    "Q": (r"\mathcal{Q}", 0),
}


# --------------------------------------------------------------------------- #
# Small LaTeX parsing helpers
# --------------------------------------------------------------------------- #
def read_braced(s, i):
    """Given s[i] == '{', return (inner_content, index_after_closing_brace)."""
    assert s[i] == "{"
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
        j += 1
    return s[i + 1 :], len(s)


def drop_command(text, name):
    """Remove every \\name[opt]{...} occurrence (with balanced braces)."""
    out = []
    pat = re.compile(r"\\" + name + r"\b")
    i = 0
    while i < len(text):
        m = pat.search(text, i)
        if not m:
            out.append(text[i:])
            break
        out.append(text[i : m.start()])
        j = m.end()
        # optional [..]
        while j < len(text) and text[j] in " \t":
            j += 1
        if j < len(text) and text[j] == "[":
            k = text.find("]", j)
            if k != -1:
                j = k + 1
        while j < len(text) and text[j] in " \t\n":
            j += 1
        if j < len(text) and text[j] == "{":
            _, j = read_braced(text, j)
        i = j
    return "".join(out)


def keep_last_arg(text, name, nargs):
    """Replace \\name{a}{b}...{last} with the content of its last argument.

    Used for \\multirow / \\multicolumn so tables survive pandoc.
    """
    pat = re.compile(r"\\" + name + r"\b")
    while True:
        m = pat.search(text)
        if not m:
            return text
        j = m.end()
        last = ""
        for _ in range(nargs):
            while j < len(text) and text[j] in " \t\n":
                j += 1
            if j < len(text) and text[j] == "*":
                last = ""
                j += 1
                continue
            if j < len(text) and text[j] == "{":
                last, j = read_braced(text, j)
            elif j < len(text) and text[j] == "[":
                _, j = read_braced(text, j)  # not expected; be lenient
        text = text[: m.start()] + last + text[j:]


# --------------------------------------------------------------------------- #
# Flatten \input
# --------------------------------------------------------------------------- #
def flatten(path, seen=None):
    seen = seen or set()
    path = path.resolve()
    text = path.read_text(encoding="utf-8", errors="replace")

    def repl(m):
        target = m.group(1).strip()
        if not target.endswith(".tex"):
            target += ".tex"
        sub = (GUIDE / target)
        if not sub.exists():
            sub = (path.parent / target)
        if not sub.exists() or sub in seen:
            return ""
        seen.add(sub)
        return flatten(sub, seen)

    return re.sub(r"\\(?:input|include)\{([^}]*)\}", repl, text)


# --------------------------------------------------------------------------- #
# Macros: parse \newcommand / \DeclareMathOperator
# --------------------------------------------------------------------------- #
def parse_newcommands(tex):
    cmds = {}
    for m in re.finditer(r"\\(?:re)?newcommand\*?", tex):
        i = m.end()
        while i < len(tex) and tex[i] in " \t":
            i += 1
        if i >= len(tex):
            continue
        if tex[i] == "{":
            grp, i = read_braced(tex, i)
            name = grp.strip()
        else:
            nm = re.match(r"\\[A-Za-z]+\*?", tex[i:])
            if not nm:
                continue
            name = nm.group(0)
            i += nm.end()
        name = name.lstrip("\\")
        while i < len(tex) and tex[i] in " \t":
            i += 1
        nargs = 0
        if i < len(tex) and tex[i] == "[":
            am = re.match(r"\[(\d+)\]", tex[i:])
            if am:
                nargs = int(am.group(1))
                i += am.end()
            while i < len(tex) and tex[i] in " \t":
                i += 1
            if i < len(tex) and tex[i] == "[":  # optional-arg default; skip
                _, i = read_braced_bracket(tex, i)
        while i < len(tex) and tex[i] in " \t\n":
            i += 1
        if i < len(tex) and tex[i] == "{":
            body, i = read_braced(tex, i)
            if name and name not in MACRO_BLACKLIST:
                cmds[name] = (nargs, body)
    return cmds


def read_braced_bracket(s, i):
    assert s[i] == "["
    depth = 0
    j = i
    while j < len(s):
        if s[j] == "[":
            depth += 1
        elif s[j] == "]":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
        j += 1
    return s[i + 1 :], len(s)


def parse_operators(tex):
    ops = {}
    for m in re.finditer(r"\\DeclareMathOperator(\*?)\s*\{\\([A-Za-z]+)\}", tex):
        star = m.group(1)
        name = m.group(2)
        i = m.end()
        while i < len(tex) and tex[i] in " \t":
            i += 1
        if i < len(tex) and tex[i] == "{":
            body, _ = read_braced(tex, i)
            ops[name] = r"\operatorname%s{%s}" % (star, body)
    return ops


def collect_macros():
    """Return (prepend_tex, mathjax_macros) from math.tex + guide.tex."""
    sources = (GUIDE / "math.tex").read_text() + "\n" + (GUIDE / "guide.tex").read_text()
    cmds = parse_newcommands(sources)
    ops = parse_operators(sources)

    # LaTeX preamble snippet for pandoc to expand text + math macros itself.
    lines = []
    for name, (nargs, body) in cmds.items():
        if nargs:
            lines.append(r"\newcommand{\%s}[%d]{%s}" % (name, nargs, body))
        else:
            lines.append(r"\newcommand{\%s}{%s}" % (name, body))
    prepend = "\n".join(lines)

    # MathJax macros (fallback for anything pandoc leaves, esp. operators).
    mj = {}
    for name, (nargs, body) in cmds.items():
        mj[name] = (body, nargs)
    for name, body in ops.items():
        mj[name] = (body, 0)
    for name, (body, nargs) in MATHJAX_EXTRA.items():
        mj[name] = (body, nargs)
    for name in MATHJAX_DROP:
        mj.pop(name, None)
    return prepend, mj


def mathjax_macros_js(mj):
    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    out = []
    for name, (body, nargs) in sorted(mj.items()):
        if nargs:
            out.append('\t\t\t\t\t"%s": ["%s", %d]' % (name, esc(body), nargs))
        else:
            out.append('\t\t\t\t\t"%s": "%s"' % (name, esc(body)))
    return ",\n".join(out)


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #
IMG_EXT_TRY = [".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps"]


def resolve_image(ref):
    p = (GUIDE / ref)
    if p.suffix and p.exists():
        return p
    for ext in IMG_EXT_TRY:
        cand = GUIDE / (ref + ext) if not p.suffix else p.with_suffix(ext)
        if cand.exists():
            return cand
    # maybe ref already had an extension that doesn't exist; try basename swaps
    if p.suffix:
        for ext in IMG_EXT_TRY:
            cand = p.with_suffix(ext)
            if cand.exists():
                return cand
    return None


def convert_image(src):
    """Convert src into FIGS_OUT, return the web path relative to guide/."""
    rel = src.relative_to(GUIDE)
    # map pages/imgs/* and figs/* under assets/figs/, preserving sub-structure
    parts = list(rel.parts)
    if parts[0] == "figs":
        parts = parts[1:]
    elif parts[0] == "pages":
        parts = parts[1:]  # drop 'pages'
    sub = Path(*parts)
    ext = src.suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg"):
        dest = FIGS_OUT / sub
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    elif ext == ".pdf":
        dest = (FIGS_OUT / sub).with_suffix(".svg")
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["pdftocairo", "-svg", str(src), str(dest)], check=True)
    elif ext == ".eps":
        dest = (FIGS_OUT / sub).with_suffix(".svg")
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["inkscape", str(src), "--export-type=svg", "--export-filename=" + str(dest)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        dest = FIGS_OUT / sub
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return "assets/figs/" + str(dest.relative_to(FIGS_OUT)).replace("\\", "/")


def process_images(text):
    refs = set(re.findall(r"\\includegraphics(?:\s*\[[^\]]*\])?\s*\{([^}]*)\}", text))
    mapping = {}
    for ref in refs:
        src = resolve_image(ref.strip())
        if not src:
            print("  WARNING: image not found:", ref, file=sys.stderr)
            continue
        mapping[ref] = convert_image(src)

    def repl(m):
        opts = m.group(1) or ""
        ref = m.group(2).strip()
        web = mapping.get(ref)
        if not web:
            return ""
        # keep only width-ish options pandoc understands; drop trim/clip/scale
        keep = ""
        wm = re.search(r"width\s*=\s*[^,\]]+", opts)
        if wm:
            keep = "[" + wm.group(0) + "]"
        return r"\includegraphics%s{%s}" % (keep, web)

    return re.sub(
        r"\\includegraphics(\s*\[[^\]]*\])?\s*\{([^}]*)\}", repl, text
    )


# --------------------------------------------------------------------------- #
# Raw-HTML stashing (boxes, algorithms) via sentinels that survive pandoc
# --------------------------------------------------------------------------- #
class Stash:
    def __init__(self):
        self.blocks = []

    def add(self, html_str):
        token = "ZZRAWHTMLZZ%dZZ" % len(self.blocks)
        self.blocks.append(html_str)
        return "\n\n%s\n\n" % token

    def restore(self, html_doc):
        for k, block in enumerate(self.blocks):
            token = "ZZRAWHTMLZZ%dZZ" % k
            html_doc = re.sub(
                r"<p>\s*" + token + r"\s*</p>", lambda m: block, html_doc
            )
            html_doc = html_doc.replace(token, block)
        return html_doc


def convert_boxes(text, stash, daynum):
    """Wrap theorem-like environments in styled divs with numbered titles."""
    counters = {k: 0 for k in BOX_ENVS}
    for env, (cls, label) in BOX_ENVS.items():
        # process one environment type at a time, supporting optional [title]
        pattern = re.compile(
            r"\\begin\{%s\}(\[[^\]]*\])?(.*?)\\end\{%s\}" % (env, env), re.DOTALL
        )

        def repl(m):
            counters[env] += 1
            extra = m.group(1) or ""
            extra = extra[1:-1].strip() if extra else ""
            body = m.group(2)
            title = "%s %d.%d" % (label, daynum, counters[env])
            if extra:
                title += " (%s)" % extra
            open_html = '<div class="guide-box %s"><div class="guide-box-title">%s</div>' % (
                cls,
                html.escape(title),
            )
            # Re-run pandoc-relevant content through as LaTeX: keep body inline by
            # stashing only the wrapper, letting pandoc convert the body.
            return stash.add(open_html) + body + stash.add("</div>")

        text = pattern.sub(repl, text)
    return text


# --- algorithms ---------------------------------------------------------- #
ALG_BLOCK = re.compile(r"\\begin\{algorithm\}(\[[^\]]*\])?(.*?)\\end\{algorithm\}", re.DOTALL)
ALGIC = re.compile(r"\\begin\{algorithmic\}(\[[^\]]*\])?(.*?)\\end\{algorithmic\}", re.DOTALL)


def math_to_mathjax(s):
    # $$...$$ -> \( ... \)  (handle display dollars before single dollars)
    s = re.sub(r"\$\$(.+?)\$\$", lambda m: r"\(" + m.group(1) + r"\)", s, flags=re.DOTALL)
    # $...$ -> \( ... \)
    return re.sub(r"\$(.+?)\$", lambda m: r"\(" + m.group(1) + r"\)", s, flags=re.DOTALL)


def clean_algo_text(s):
    """Convert common LaTeX text formatting in algorithm lines to HTML.

    Algorithm blocks bypass pandoc, so basic markup must be handled here.
    """
    for cmd, tag in (("textbf", "b"), ("textsc", "b"), ("emph", "i"),
                     ("textit", "i"), ("texttt", "code"), ("texttt", "code")):
        s = re.sub(r"\\%s\{([^{}]*)\}" % cmd, r"<%s>\1</%s>" % (tag, tag), s)
    s = re.sub(r"\{\\(?:bfseries|bf)\s+([^{}]*)\}", r"<b>\1</b>", s)
    s = re.sub(r"\{\\(?:itshape|it|em)\s+([^{}]*)\}", r"<i>\1</i>", s)
    s = s.replace("~", " ").replace(r"\\", " ").replace(r"\,", " ")
    return s


def convert_algorithmic(body):
    """Turn an algorithmic body into indented HTML lines (MathJax-friendly)."""
    # normalise whitespace/newlines but keep tokens
    s = body.replace("\n", " ")
    # tokenise on the algorithmic control words
    tokens = re.split(
        r"(\\STATE|\\REQUIRE|\\ENSURE|\\RETURN|\\FOR|\\ENDFOR|\\WHILE|\\ENDWHILE"
        r"|\\IF|\\ELSIF|\\ELSE|\\ENDIF|\\LOOP|\\ENDLOOP|\\REPEAT|\\UNTIL)",
        s,
    )
    lines = []
    indent = 0
    num = 0
    i = 1  # tokens[0] is preamble before first command
    while i < len(tokens):
        cmd = tokens[i]
        arg = tokens[i + 1] if i + 1 < len(tokens) else ""
        i += 2

        def cond(a):
            a = a.strip()
            m = re.match(r"\{(.*)\}(.*)", a, re.DOTALL)
            if m:
                return m.group(1).strip(), m.group(2).strip()
            return "", a.strip()

        if cmd in (r"\STATE", r"\REQUIRE", r"\ENSURE", r"\RETURN"):
            prefix = {
                r"\REQUIRE": "<b>Require:</b> ",
                r"\ENSURE": "<b>Ensure:</b> ",
                r"\RETURN": "<b>return</b> ",
            }.get(cmd, "")
            num += 1
            lines.append((indent, prefix + arg.strip(), num))
        elif cmd in (r"\FOR", r"\WHILE", r"\IF", r"\LOOP", r"\REPEAT"):
            c, _ = cond(arg)
            kw = {
                r"\FOR": ("for", "do"),
                r"\WHILE": ("while", "do"),
                r"\IF": ("if", "then"),
                r"\LOOP": ("loop", ""),
                r"\REPEAT": ("repeat", ""),
            }[cmd]
            num += 1
            txt = "<b>%s</b> %s %s" % (kw[0], c, ("<b>%s</b>" % kw[1] if kw[1] else ""))
            lines.append((indent, txt.strip(), num))
            indent += 1
        elif cmd in (r"\ELSE", r"\ELSIF"):
            c, _ = cond(arg)
            indent = max(0, indent - 1)
            num += 1
            if cmd == r"\ELSIF":
                lines.append((indent, "<b>else if</b> %s <b>then</b>" % c, num))
            else:
                lines.append((indent, "<b>else</b>", num))
            indent += 1
        elif cmd in (r"\ENDFOR", r"\ENDWHILE", r"\ENDIF", r"\ENDLOOP", r"\UNTIL"):
            indent = max(0, indent - 1)
            if cmd == r"\UNTIL":
                c, _ = cond(arg)
                num += 1
                lines.append((indent, "<b>until</b> %s" % c, num))
    # render
    rows = []
    for ind, txt, n in lines:
        txt = clean_algo_text(txt)
        txt = math_to_mathjax(txt)
        rows.append(
            '<div class="algo-line" style="padding-left:%.1fem">'
            '<span class="algo-num">%d:</span> %s</div>' % (ind * 1.5 + 1.5, n, txt)
        )
    return "\n".join(rows)


def convert_algorithms(text, stash, daynum):
    counter = [0]

    def repl(m):
        counter[0] += 1
        inner = m.group(2)
        cap_m = re.search(r"\\caption\{", inner)
        caption = ""
        if cap_m:
            caption, _ = read_braced(inner, inner.index("{", cap_m.start()))
            caption = re.sub(r"\\label\{[^}]*\}", "", caption)
            caption = clean_algo_text(caption.strip())
            caption = math_to_mathjax(caption)
        algm = ALGIC.search(inner)
        body_html = convert_algorithmic(algm.group(2)) if algm else ""
        title = "Algorithm %d.%d" % (daynum, counter[0])
        if caption:
            title += ": " + caption
        block = (
            '<figure class="guide-algorithm"><figcaption>%s</figcaption>'
            '<div class="algo-body">%s</div></figure>' % (title, body_html)
        )
        return stash.add(block)

    text = ALG_BLOCK.sub(repl, text)

    # standalone algorithmic (rare)
    def repl2(m):
        counter[0] += 1
        block = (
            '<figure class="guide-algorithm"><figcaption>Algorithm %d.%d</figcaption>'
            '<div class="algo-body">%s</div></figure>'
            % (daynum, counter[0], convert_algorithmic(m.group(2)))
        )
        return stash.add(block)

    return ALGIC.sub(repl2, text)


# --------------------------------------------------------------------------- #
# Preprocess one chapter's flattened LaTeX
# --------------------------------------------------------------------------- #
def dedupe_labels(text):
    """Rename duplicate \\label definitions so MathJax (tags:'ams') doesn't error.

    The guide source has a few labels defined twice (e.g. eq:LogLingCR1). MathJax
    raises 'Label ... multiply defined'. Keep the first occurrence (refs resolve to
    it) and make later ones unique.
    """
    seen = {}

    def repl(m):
        name = m.group(1)
        seen[name] = seen.get(name, 0) + 1
        if seen[name] == 1:
            return m.group(0)
        return r"\label{%s--dup%d}" % (name, seen[name])

    return re.sub(r"\\label\{([^}]*)\}", repl, text)


def preprocess(text, prepend, daynum):
    stash = Stash()
    # rename duplicate labels (source bugs) before anything else
    text = dedupe_labels(text)
    # drop editor/margin macros
    for cmd in DROP_CMDS:
        text = drop_command(text, cmd)
    # tables: collapse multirow/multicolumn to their content
    text = keep_last_arg(text, "multirow", 3)
    text = keep_last_arg(text, "multicolumn", 3)
    # algorithms BEFORE code (they may contain $...$ but not python blocks)
    text = convert_algorithms(text, stash, daynum)
    # python listings -> lstlisting (pandoc emits verbatim code blocks)
    text = re.sub(r"\\begin\{python\}(\[[^\]]*\])?", r"\\begin{lstlisting}[language=Python]", text)
    text = text.replace(r"\end{python}", r"\end{lstlisting}")
    # theorem-like boxes
    text = convert_boxes(text, stash, daynum)
    # prepend macro definitions so pandoc expands text + math macros
    text = prepend + "\n\n" + text
    return text, stash


# --------------------------------------------------------------------------- #
# Pandoc
# --------------------------------------------------------------------------- #
def run_pandoc(tex):
    tmp = GUIDE / "_chapter_tmp.tex"
    tmp.write_text(tex, encoding="utf-8")
    try:
        proc = subprocess.run(
            [
                "pandoc",
                "-f", "latex",
                "-t", "html5",
                "--mathjax",
                "--no-highlight",
                "--shift-heading-level-by=1",
                "--citeproc",
                "--bibliography", str(BIB),
                "-M", "link-citations=true",
                "-M", "reference-section-title=References",
                str(tmp.name),
            ],
            cwd=str(GUIDE),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            raise SystemExit("pandoc failed")
        if proc.stderr.strip():
            print(proc.stderr.strip(), file=sys.stderr)
        return proc.stdout
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# HTML post-processing
# --------------------------------------------------------------------------- #
def fix_code_blocks(html_doc):
    # move language class onto <code> for highlight.js; mark others nohighlight
    html_doc = re.sub(
        r'<pre class="python"[^>]*>\s*<code>',
        '<pre><code class="language-python">',
        html_doc,
    )
    # any remaining plain <pre><code> (verbatim/CLI) -> nohighlight
    html_doc = re.sub(
        r"<pre><code>", '<pre><code class="nohighlight">', html_doc
    )
    return html_doc


def build_toc(html_doc):
    items = re.findall(r'<h([23]) id="([^"]+)"[^>]*>(.*?)</h\1>', html_doc, re.DOTALL)
    if not items:
        return ""
    lines = ["<nav>", "<ul>"]
    for level, hid, label in items:
        label = re.sub(r"<[^>]+>", "", label).strip()
        cls = "toc-h2" if level == "2" else "toc-h3"
        lines.append('<li><a class="%s" href="#%s">%s</a></li>' % (cls, hid, label))
    lines.append("</ul>")
    lines.append("</nav>")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def page_nav(idx):
    parts = ['<nav class="guide-pagenav">']
    if idx > 0:
        slug, day, title, _ = CHAPTERS[idx - 1]
        parts.append(
            '<a class="prev" href="%s.html"><span class="label">Previous</span>'
            '<span class="title">&larr; %s &middot; %s</span></a>' % (slug, day, title)
        )
    else:
        parts.append("<span></span>")
    if idx < len(CHAPTERS) - 1:
        slug, day, title, _ = CHAPTERS[idx + 1]
        parts.append(
            '<a class="next" href="%s.html"><span class="label">Next</span>'
            '<span class="title">%s &middot; %s &rarr;</span></a>' % (slug, day, title)
        )
    else:
        parts.append("<span></span>")
    parts.append("</nav>")
    return "\n".join(parts)


def render_page(template, *, title, macros_js, sidebar, content, pagenav):
    out = template.replace("__PAGE_TITLE__", title)
    out = out.replace("__MATHJAX_MACROS__", macros_js)
    out = out.replace("__SIDEBAR__", sidebar)
    out = out.replace("__CONTENT__", content)
    out = out.replace("__PAGENAV__", pagenav)
    return out


def build_landing(template, macros_js):
    cards = []
    sidebar_items = ["<nav>", "<ul>"]
    for slug, day, title, _ in CHAPTERS:
        cards.append(
            '<div class="col-md-6 mb-4"><a class="guide-day-card" href="%s.html">'
            '<div class="day-num">%s</div><div class="day-title">%s</div></a></div>'
            % (slug, day, title)
        )
        sidebar_items.append('<li><a class="toc-h2" href="%s.html">%s &middot; %s</a></li>' % (slug, day, title))
    sidebar_items += ["</ul>", "</nav>"]
    content = (
        '<h1>LxMLS Lab Guide</h1>'
        "<p>This is the web edition of the Lisbon Machine Learning Summer School lab "
        "guide. It walks through the hands-on exercises for each day of the school, "
        "with copy-able code, rendered equations, and figures. Use the cards below or "
        "the sidebar to navigate.</p>"
        '<div class="row mt-4">' + "".join(cards) + "</div>"
    )
    return render_page(
        template,
        title="Lab Guide",
        macros_js=macros_js,
        sidebar="\n".join(sidebar_items),
        content=content,
        pagenav="",
    )


def main():
    print("Collecting macros...")
    prepend, mj = collect_macros()
    macros_js = mathjax_macros_js(mj)
    template = (WEB / "template.html").read_text()

    # fresh output dirs
    if OUT.exists():
        shutil.rmtree(OUT)
    FIGS_OUT.mkdir(parents=True, exist_ok=True)

    # copy static assets
    shutil.copy2(WEB / "guide.css", ASSETS / "guide.css")
    shutil.copy2(WEB / "copy-button.js", ASSETS / "copy-button.js")
    shutil.copytree(VENDOR / "mathjax", ASSETS / "mathjax")
    shutil.copytree(VENDOR / "highlight", ASSETS / "highlight")

    for idx, (slug, day, title, src) in enumerate(CHAPTERS):
        print("Building %s (%s)..." % (slug, src))
        flat = flatten(GUIDE / src)
        flat = process_images(flat)
        pre, stash = preprocess(flat, prepend, idx)
        body = run_pandoc(pre)
        body = stash.restore(body)
        body = fix_code_blocks(body)
        toc = build_toc(body)
        page_title = "%s &middot; %s" % (day, title)
        content = "<h1>%s <small class=\"text-muted\">%s</small></h1>\n%s" % (
            title, day, body,
        )
        page = render_page(
            template,
            title="%s %s" % (day, title),
            macros_js=macros_js,
            sidebar=toc,
            content=content,
            pagenav=page_nav(idx),
        )
        (OUT / (slug + ".html")).write_text(page, encoding="utf-8")

    print("Building landing page...")
    (OUT / "index.html").write_text(build_landing(template, macros_js), encoding="utf-8")
    print("Done. Output in", OUT)


if __name__ == "__main__":
    main()
