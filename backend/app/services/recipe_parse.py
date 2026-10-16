"""Deterministic recipe extraction from pasted text or HTML — no AI provider.

This is the "paste a recipe" path. It exists for the same reason
``extract_pasted_recipe`` does: the content is already in front of us, so
spending an LLM call to re-derive it is slower, costs money, can get it wrong,
and — worst — fails outright with "no AI provider configured" for anyone who
never set one up. Deterministic first, model last.

Three shapes arrive here, in descending order of how much the source tells us:

1. **Microdata** (``itemprop="recipeIngredient"``). Unlike JSON-LD this SURVIVES
   a browser copy: it lives in attributes on visible elements, whereas
   ``<script type="application/ld+json">`` is stripped by every browser when you
   select a page and hit copy. Mapped to a schema.org node so it goes through the
   one existing normalizer.
2. **Styled HTML** — what the clipboard actually holds after copying a rendered
   recipe: divs, spans, inline styles, a ``<style>`` block, and class names like
   ``ingredients``. Flattened to one line per element, keeping the list/heading
   structure that makes the text parseable, then handed to (3).
3. **A plain-text blob.** Parsed by structure: section headings, numbered steps,
   and — when a source has no headings at all — by classifying each line.

Anything this module can't confidently read returns ``None`` so the caller falls
through to the AI path unchanged.
"""
import re
import unicodedata

from bs4 import BeautifulSoup, NavigableString
from bs4.element import CData

from . import units
# The app's one cooking-action vocabulary (Mix/Fold/Chill/Bake/Preheat/Cool),
# used by _until_tail to tell a mid-method advisory from the trailing Notes
# block. Imported, never re-listed. ``step_structure`` is pure and imports only
# ``cooking``, so there is no cycle back to here.
from .step_structure import infer_phase

# Section headings people actually write, including the ALL-CAPS and
# "INGREDIENTS:" variants. Matched on a short line only, so an instruction that
# happens to begin "Method: warm the milk" isn't mistaken for a heading.
_ING_HEAD = re.compile(
    r"^(?:ingredients?|you(?:'?ll)?\s+(?:will\s+)?need|what\s+you(?:'?ll)?\s+need"
    r"|shopping\s+list"
    # The commonest non-English headings. Cheap to recognise, and without them a
    # German or French recipe had its headings imported AS ingredients — worse
    # than the old behaviour, where it went to the model and came back correct.
    r"|zutaten|ingr(?:e|é)dients?|ingredienti|ingredientes)\b\s*:?\s*$", re.I)
_STEP_HEAD = re.compile(
    r"^(?:method|instructions?|directions?|steps?|preparation|procedure"
    r"|how\s+to\s+make(?:\s+it)?|to\s+make"
    r"|zubereitung|pr(?:e|é)paration|preparazione|preparaci(?:o|ó)n)\b\s*:?\s*$", re.I)
# Headings that END the recipe body. A trailing "Notes"/"Tips" section was being
# imported as further method steps, because nothing terminated the step body.
_TAIL_HEAD = re.compile(
    r"^(?:notes?|tips?|tip[s]?\s*&\s*tricks|variations?|storage|to\s+store"
    r"|make\s+ahead|nutrition(?:\s+information)?|equipment|comments?|reviews?"
    r"|related|you\s+might\s+also\s+like)\b\s*:?\s*$", re.I)
# A sub-heading inside the ingredient list: "For the sauce:", "Topping:", or a
# bare "For the topping" with no colon. schema.org has no field for these but
# RecipeIngredient.section does, so they're captured rather than flattened away.
# The author's own wording is kept verbatim (minus the colon) — "For the drizzle"
# is how a recipe book labels it, and rewriting it to "drizzle" would be us
# editing their text for no gain.
_SUB_HEAD = re.compile(r"^(?:(?P<colon>.+?)\s*:|for\s+the\s+.+?)\s*$", re.I)

_STEP_NUMBER = re.compile(r"^\s*(?:step\s*)?(\d{1,2})\s*[.):\-–]\s+", re.I)
_HEADING_MAX = 60          # a heading is a short line, not a sentence

# "Serves 8", "Serves: 8-10", "8 servings", "Yield: 12 cookies", "Makes 24"
_SERVES = re.compile(
    r"\b(?:serves|servings?|yield|makes|portions?)\b\s*:?\s*(?P<n>\d+)"
    r"|(?P<n2>\d+)\s*(?:servings?|portions?)\b", re.I)
# "Prep 20 min", "Cook time: 1 hr 15 mins", "Total: 1 hour"
_DURATION_LABEL = {
    "prep": ("prep", "preparation", "active", "hands-on", "hands on"),
    "cook": ("cook", "cooking", "bake", "baking", "roast", "roasting", "grill"),
    "total": ("total", "ready in", "overall"),
}


# Elements that render INSIDE a line of text rather than starting one. Their
# text belongs to the line of the block that contains them, so they must be
# joined into it before anything splits on newlines. ``soup.get_text("\n")``
# cannot do this: it puts the separator at EVERY string boundary, so
# ``<li><span>240 grams</span><span>Egg Whites</span><span>(240 g)</span></li>``
# came out as three lines and the amount was orphaned from its food — on the
# DEFAULT paste path, because copying from a browser puts HTML on the clipboard.
#
# Written out rather than guessed at, grouped by why each is here. This is the
# HTML phrasing-content set minus the controls/junk already decomposed above:
#   text styling        b i u s em strong small big tt font strike
#   semantic spans      a span abbr acronym cite code data dfn kbd mark q samp
#                       time var bdi bdo label output nobr
#   edits (transparent) ins del
#   scripts/annotation  sub sup ruby rt rp
#   replaced/void       img picture source track map area meter progress
#                       canvas object embed iframe video audio math
# ``sub``/``sup`` matter specifically: a page renders the fraction ½ as
# ``<sup>1</sup>⁄<sub>2</sub>``, and splitting that put "1", "⁄" and "2" on
# three lines of their own.
# The replaced/void group matters because ``img`` is inline: WordPress rewrites
# emoji in post content to ``<img class="wp-smiley">`` and unit/diet icons sit
# inside ingredient rows, so ``<li>2 cups <img> flour</li>`` split into "2 cups"
# and "flour" — the headline bug, by another route. ``svg`` behaves only because
# it is decomposed as junk below; ``img`` was not, which is the asymmetry.
#
# Anything NOT in this set stays a line boundary — which is exactly today's
# behaviour for every element — so an unrecognised custom tag can never start
# silently swallowing the following line. ``br`` and ``wbr`` are inline but are
# handled separately below, because they are about breaking, not about text.
_INLINE_TAGS = frozenset({
    "a", "abbr", "acronym", "b", "bdi", "bdo", "big", "cite", "code", "data",
    "del", "dfn", "em", "font", "i", "ins", "kbd", "label", "mark", "nobr",
    "output", "q", "rp", "rt", "ruby", "s", "samp", "small", "span", "strike",
    "strong", "sub", "sup", "time", "tt", "u", "var",
})

# Replaced inline elements: inline-level, but their subtree is FALLBACK that a
# working browser never renders ("Your browser cannot play video"). So they are
# a pure glue point — no line break, and no text either. Treating them as
# ordinary inline would join that fallback copy into the ingredient row, which
# is worse than the junk line it used to produce. This is how ``svg`` already
# behaves by being decomposed as junk; these were simply never listed.
#
# ``math`` is here for a different reason: MathML content IS rendered, but its
# ~30 token elements (mn, mi, mo, mrow, …) are none of them in _INLINE_TAGS, so
# a walked formula split onto one line per token. Treating the formula as
# opaque is the cheap correct-enough answer — a recipe states quantities as
# text, not as MathML — and it drops a junk line the old code also produced.
_REPLACED_TAGS = frozenset({
    "area", "audio", "canvas", "embed", "iframe", "img", "map", "math",
    "meter", "object", "picture", "progress", "source", "track", "video",
})

# Inline elements a page uses as a LEAD-IN HEADING: "<p><strong>Notes</strong>
# Keep refrigerated…</p>" is mainstream recipe-blog markup. Joining the bold
# into its paragraph is typographically right but destroys the heading as a
# LINE, and the heading-as-a-line is what _TAIL_HEAD/_SUB_HEAD match on — so
# "Notes" stopped terminating the method and came back as a third step, and
# "For the sauce:" stopped becoming a section and corrupted an ingredient's
# display text instead. A recognised heading therefore keeps its own line.
_LEAD_IN_TAGS = frozenset({"b", "strong", "em", "i", "u", "span", "mark"})

_WS_RUN = re.compile(r"\s+")
# An element that preserves its own newlines: <pre>, or anything the author
# styled with white-space:pre*. The styled case is not exotic — it is how the
# clipboard's text/html flavour represents a copied PLAIN-TEXT recipe, and
# since the UI posts that flavour in preference to the plain one, collapsing
# those newlines turned a perfectly parseable paste into one unreadable line
# and sent the user to the "no AI provider" dead end this module exists to
# avoid. The style attribute survives even though <style> blocks are stripped.
_PRE_WS = re.compile(
    r"white-space\s*:\s*(?:pre|pre-wrap|pre-line|break-spaces)\b", re.I)
# Characters that can only END a token, so a word character after one is a new
# token and needs a space ("(240 g)" + "Egg Whites" on metric-first layouts).
_TOKEN_END = frozenset(")]}%;:!?")
# The two that are ALSO decimal separators — "." in en, "," across most of
# Europe — so they end a token only when a LETTER precedes them. That spaces
# "2 tsp." + "Vanilla extract" while leaving "1" + "." + "5" as "1.5" and
# "1," + "5 kg" as "1,5 kg".
_TOKEN_END_AFTER_LETTER = frozenset(".,")
# Pushed onto the walk stack to mark "this block's content has all been
# consumed, end its line here", and to leave a newline-preserving region.
_FLUSH = object()
_POP_PRE = object()


def _preserves_newlines(tag) -> bool:
    """Whether this element's own newlines are significant (``<pre>``-like)."""
    return tag.name == "pre" or bool(_PRE_WS.search(tag.get("style") or ""))


def _is_lead_in_heading(text: str) -> bool:
    """Whether a bolded lead-in is one of the headings the text parser matches.

    Kept to the SAME four patterns the parser already uses, so there is one
    heading vocabulary rather than a second one that can drift. Length-capped
    by ``_HEADING_MAX`` for the same reason ``_is_sub_heading`` is: ``_SUB_HEAD``
    matches anything ending in a colon, and a long bolded sentence that happens
    to end in one is prose, not a heading.
    """
    return bool(text) and len(text) <= _HEADING_MAX and bool(
        _ING_HEAD.match(text) or _STEP_HEAD.match(text)
        or _TAIL_HEAD.match(text) or _SUB_HEAD.match(text))


def _needs_join_space(left: str, right: str) -> bool:
    """Whether a space goes between two inline chunks glued with no whitespace.

    The rule is: only when BOTH characters at the boundary are alphanumeric.
    Derived from what the three real shapes need, not from taste —

    * ``<span>240 grams</span><span>Egg Whites</span>`` → ``s`` | ``E``, both
      word characters, so a space. Without it you get "240 gramsEgg Whites",
      which is the exact corruption the plain-text sibling fix had to repair
      downstream; better not to create it here in the first place.
    * ``<span>Egg Whites</span><span>(240 g)</span>`` → ``s`` | ``(``: a
      bracket already reads as a boundary, so no space. This also keeps the
      output byte-identical to what the run-on text segmenter produces.
    * ``<sup>1</sup><span>⁄</span><sub>2</sub>`` → ``1`` | ``⁄`` and ``⁄`` |
      ``2``. U+2044 FRACTION SLASH is punctuation, not alphanumeric, so "1⁄2"
      stays one token instead of becoming "1 ⁄ 2" (verified: ``'⁄'.isalnum()``
      is False while ``'¹'`` and ``'₂'`` are True, so the superscript/subscript
      spelling of the same fraction behaves identically).
    * ``Jell<span>-</span>O`` → ``l`` | ``-`` and ``-`` | ``O``: no space at
      either side, so the hyphenated name survives.

    Trailing COMBINING MARKS are skipped to find the real character, because a
    macOS clipboard hands over decomposed text: "café" arrives as ``e`` plus
    U+0301, whose category is Mn and which is therefore not alnum. Testing the
    last code unit naively made ``<span>café</span><span>Beans</span>`` come
    out as "caféBeans" while the precomposed spelling of the same word got its
    space — accented food names are far too common for that to be acceptable.

    A CLOSING punctuation mark on the left also ends a token, so it counts as a
    word character for this purpose (``_TOKEN_END``). Without that, the two
    commonest US and metric-first ingredient layouts stayed glued:
    ``<span>2 tsp.</span><span>Vanilla extract</span>`` → "2 tsp.Vanilla
    extract" and ``<span>(240 g)</span><span>Egg Whites</span>`` →
    "(240 g)Egg Whites". ``.`` and ``,`` are the awkward pair — they end
    "tsp." and "flour," but also sit inside "1.5" and the European "1,5" — so
    they only count when a LETTER precedes them.

    Known trade-off: a word deliberately split mid-way across two tags for
    styling ("Choco"+"late") gains a space. That is rare, cosmetic and still
    human-readable; the opposite error destroys the amount on every row of an
    ingredient list, so the rule errs towards inserting. A ``right`` that opens
    with a combining mark is the reverse case — it continues the previous
    letter — and correctly gets no space, since a mark is not alnum.

    Deliberately NOT handled: a trailing format character (ZWSP U+200B, soft
    hyphen U+00AD). They are break OPPORTUNITIES whose two meanings pull
    opposite ways — ZWSP usually separates tokens and would want a space, a
    soft hyphen is explicitly intra-word and must not get one — so guessing
    would be worse than the status quo of leaving both glued.
    """
    if not left or not right or not right[0].isalnum():
        return False
    i = len(left) - 1
    while i > 0 and unicodedata.category(left[i]).startswith("M"):
        i -= 1
    last = left[i]
    if last.isalnum() or last in _TOKEN_END:
        return True
    # "." ends an abbreviated unit ("tsp.") but also joins a decimal; "," ends
    # a qualifier ("flour,") but is the decimal separator across Europe.
    return (last in _TOKEN_END_AFTER_LETTER
            and i > 0 and left[i - 1].isalpha())


def _flatten_blocks(root) -> list[str]:
    """Visible text as one line per BLOCK element, inline descendants joined in.

    Whitespace inside a block is collapsed, because HTML says it is
    insignificant: a pretty-printed ``<li>\\n  <span>240 grams</span>\\n
    <span>Egg Whites</span>\\n</li>`` has to yield the same single line as the
    minified spelling, and splitting on the source's own newlines is what made
    those two disagree.
    """
    lines: list[str] = []
    buf: list[str] = []
    # The tail of what has been emitted into buf, for the join rule. A few
    # characters rather than one: the last code unit may be a combining mark
    # and _needs_join_space has to look back past it for the base letter.
    tail = [""]
    glue = [False]         # a <wbr> just said "break here" — never add a space
    pre = [0]              # depth of enclosing newline-preserving elements

    def flush() -> None:
        line = _WS_RUN.sub(" ", "".join(buf)).strip()
        buf.clear()
        tail[0] = ""
        glue[0] = False
        if line:
            lines.append(line)

    def emit(text: str) -> None:
        if not text:
            return
        if _needs_join_space(tail[0], text) and not glue[0]:
            buf.append(" ")
        glue[0] = False
        buf.append(text)
        tail[0] = text[-8:]

    def add(text: str) -> None:
        if not pre[0] or "\n" not in text:
            emit(text)
            return
        # Inside a <pre> or a white-space:pre* region the author's own newlines
        # ARE the line structure, so each one ends a line instead of being
        # collapsed to a space.
        parts = text.split("\n")
        for part in parts[:-1]:
            emit(part)
            flush()
        emit(parts[-1])

    # An explicit stack, NOT recursion. bs4 walks the tree iteratively, so
    # ``get_text`` coped with any nesting; a recursive version of this blew up
    # with a RecursionError at ~1000 levels deep (measured), and the URL-import
    # path feeds this function pages nobody vetted. Children are pushed in
    # reverse so they pop in document order, and a block pushes its closing
    # _FLUSH before them so the flush happens after its content.
    stack: list = list(reversed(root.contents))
    while stack:
        child = stack.pop()
        if child is _FLUSH:
            flush()
            continue
        if child is _POP_PRE:
            pre[0] -= 1
            continue
        # Mirrors bs4's own ``interesting_string_types``, so comments, doctypes
        # and processing instructions stay excluded exactly as ``get_text``
        # excluded them.
        if isinstance(child, NavigableString):
            if type(child) in (NavigableString, CData):
                add(str(child))
            continue
        name = child.name
        if name == "br":
            flush()            # inline, but it IS a line break
            continue
        if name == "wbr":
            glue[0] = True     # a break OPPORTUNITY, never a space
            continue
        if name in _REPLACED_TAGS:
            continue           # inline, and its subtree is unrendered fallback
        inline = name in _INLINE_TAGS
        # A newline-preserving region is walked like anything else — only the
        # newline handling in add() changes. Walking it (rather than taking its
        # get_text()) is what keeps <br>, nested blocks, the inline-join rule
        # and the synthetic section markers working inside a <pre>.
        if _preserves_newlines(child):
            pre[0] += 1
            stack.append(_POP_PRE)
        if not inline:
            flush()
            stack.append(_FLUSH)
        elif (name in _LEAD_IN_TAGS and not "".join(buf).strip()
                and _is_lead_in_heading(child.get_text(" ").strip())):
            # A bolded heading at the START of a block keeps its own line, so
            # "<p><strong>Notes</strong> Keep refrigerated…</p>" still
            # terminates the method instead of becoming a step. "Start" means
            # no VISIBLE text yet, not an empty buffer: pretty-printed markup
            # puts "\n  " in the buffer first, and testing for empty missed
            # every indented page — which is most of them.
            stack.append(_FLUSH)
        stack.extend(reversed(child.contents))

    flush()
    return lines


def html_to_lines(html: str, limit: int = 12000, *, mark_sections: bool = True) -> str:
    """Visible text, one line per block element, with the junk removed.

    Block structure is what makes the text parseable — every ``<li>`` on its own
    line is the difference between an ingredient list and one run-on sentence —
    so this deliberately keeps newlines rather than collapsing to a paragraph.
    The corollary, and the thing that took a second fix: an INLINE element is
    not a block, so the spans a site wraps around an amount and its food name
    must be joined back into their ``<li>`` instead of becoming three lines.
    ``<style>`` goes with the other junk, which is what makes pasted CSS a no-op
    instead of garbage ingredients.

    ``<form>`` is NOT removed, only its controls: plenty of sites wrap the
    ingredient list in a form for "add to shopping list" checkboxes, and
    decomposing the form deleted the ingredients outright.

    ``mark_sections=False`` for the AI prompt — the model should see the page,
    not headings this module synthesized for its own parser.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for junk in soup(["script", "style", "nav", "footer", "header", "noscript",
                      "button", "select", "input", "textarea", "svg"]):
        junk.decompose()
    if mark_sections:
        _mark_sections(soup)
    return "\n".join(_flatten_blocks(soup))[:limit]


# class/id/itemprop values that name a recipe part. Copying a rendered page
# throws away JSON-LD but keeps these attributes, and they are a far stronger
# signal than guessing from the prose — a list of "a handful of parsley" style
# ingredients is otherwise indistinguishable from a list of short steps.
_CLASS_ING = re.compile(r"(?:^|[-_ ])(?:recipe[-_ ]?)?ingredients?(?:[-_ ]|$)", re.I)
_CLASS_STEP = re.compile(
    r"(?:^|[-_ ])(?:recipe[-_ ]?)?(?:instructions?|directions?|method|steps)(?:[-_ ]|$)",
    re.I)
# Classes that CONTAIN an ingredient word but don't label an ingredient list.
_CLASS_NOT = re.compile(r"no[-_ ]ingredients|ingredients?[-_ ](?:method|instructions?)", re.I)
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def _mark_sections(soup) -> None:
    """Insert a heading line before a list the markup itself labels.

    Emits the literal words the text parser already recognises, so there is one
    heading-detection rule rather than a second parallel mechanism.

    Three things stop it emitting spurious headings, all of which produced real
    junk rows before: nested containers (the WP-Recipe-Maker DOM nests
    ``*-ingredients-container`` around ``ul.*-ingredients``, which marked twice),
    a container whose own heading already says "Ingredients" (marking it again
    duplicated it), and per-item elements (a ``<div itemprop=recipeInstructions>``
    per step emitted one heading per step).
    """
    marked: list = []
    # Document order, outermost first, so an ancestor is always seen before its
    # descendants and the descendant can be skipped.
    for el in soup.find_all(True):
        token = " ".join(filter(None, [
            " ".join(el.get("class") or []), el.get("id") or "", el.get("itemprop") or "",
        ]))
        if not token or _CLASS_NOT.search(token):
            continue
        # Never label an individual item — only a container of them.
        if el.name in ("li", "span", "a", "strong", "em", "td", "p", "meta", "img"):
            continue
        word = ("Ingredients" if _CLASS_ING.search(token)
                else "Instructions" if _CLASS_STEP.search(token) else "")
        if not word:
            continue
        if any(m in el.parents for m in marked):
            continue          # an ancestor already labelled this region
        if any(_ING_HEAD.match(h.get_text(" ").strip())
               or _STEP_HEAD.match(h.get_text(" ").strip())
               for h in el.find_all(_HEADING_TAGS)):
            marked.append(el)
            continue          # the page prints its own heading; don't double it
        # A real element, not a "\n<word>\n" string. The newlines in a bare
        # string only worked while every string boundary became a line break;
        # now that inline content is joined into its block, a loose string
        # lands in the SIBLING's line — "12 muffins" directly before a marked
        # <li> became "12 muffins Ingredients". A <div> is a block, so it gets
        # a line of its own by the same rule as everything else.
        marker = soup.new_tag("div")
        marker.string = word
        el.insert_before(marker)
        marked.append(el)


def _itemprop_values(root, name: str) -> list[str]:
    """Text of every element carrying ``itemprop="<name>"`` under ``root``."""
    out = []
    for el in root.find_all(attrs={"itemprop": True}):
        props = str(el.get("itemprop") or "").lower().split()
        if name.lower() not in props:
            continue
        # <meta content>/<time datetime> carry the value in an attribute; a
        # visible element carries it as text.
        val = (el.get("content") or el.get("datetime") or el.get_text(" ")).strip()
        if val:
            out.append(re.sub(r"\s+", " ", val))
    return out


def extract_microdata_recipe(html: str) -> dict | None:
    """A schema.org Recipe expressed as microdata, as a JSON-LD-shaped node.

    Returned in node form on purpose: it then goes through the same
    ``normalize_jsonld`` as every other structured source, so ISO durations,
    yields and instruction flattening have exactly one implementation.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    scope = soup.find(attrs={"itemtype": re.compile(r"schema\.org/Recipe", re.I)})
    # No itemscope is fine: bare itemprop attributes are still unambiguous, and a
    # copied fragment often loses the wrapper that carried the itemtype.
    root = scope or soup
    ingredients = _itemprop_values(root, "recipeIngredient") \
        or _itemprop_values(root, "ingredients")     # pre-2017 schema.org name
    steps = _itemprop_values(root, "recipeInstructions")
    if not _enough(ingredients, steps):
        return None
    node = {
        "@type": "Recipe",
        "name": next(iter(_itemprop_values(root, "name")), ""),
        "description": next(iter(_itemprop_values(root, "description")), ""),
        "recipeIngredient": ingredients,
        "recipeInstructions": steps,
        "recipeYield": next(iter(_itemprop_values(root, "recipeYield")), ""),
        "prepTime": next(iter(_itemprop_values(root, "prepTime")), ""),
        "cookTime": next(iter(_itemprop_values(root, "cookTime")), ""),
        "totalTime": next(iter(_itemprop_values(root, "totalTime")), ""),
        "recipeCategory": _itemprop_values(root, "recipeCategory"),
        "recipeCuisine": _itemprop_values(root, "recipeCuisine"),
        "keywords": next(iter(_itemprop_values(root, "keywords")), ""),
    }
    img = soup.find(attrs={"itemprop": "image"})
    if img is not None:
        node["image"] = img.get("src") or img.get("content") or img.get_text(" ").strip()
    return node


def _enough(ingredients, steps, *, require_step: bool = False) -> bool:
    """Whether we found enough to call this a recipe rather than guessing.

    ``require_step`` is set on the unheaded path, where nothing in the source
    said "these are the ingredients" and the only evidence is that some lines
    look like amounts. A page of prose with a couple of prices in it cleared the
    old two-ingredients-and-no-method bar and imported as a recipe; a recipe with
    no method at all is not a recipe, so demand one when we are guessing.
    """
    if require_step:
        return len(ingredients) >= 2 and len(steps) >= 1
    return len(ingredients) >= 2 or (len(ingredients) >= 1 and len(steps) >= 1)


def _looks_like_ingredient(line: str) -> bool:
    """Whether a line reads as an amount of something, not as an instruction.

    Used only when the source gives no headings at all. The measurement parser is
    the primary signal — it already knows every unit spelling and fraction form
    the app supports, so this stays one vocabulary rather than a second list.
    """
    s = line.strip()
    if not s or len(s) > 120:
        return False              # instructions are sentences; ingredients aren't
    if s.endswith(":"):
        return False              # a trailing colon is a heading ("For the sauce:")
    p = units.parse_line(s)
    if p["qty"] is not None:
        # A leading number is strong — unless the line is a sentence that merely
        # starts with one ("2 minutes before serving, add the cream").
        return not (s.rstrip().endswith(".") and len(s.split()) > 12)
    # No number, but a bare unit or a very short noun phrase still reads as an
    # ingredient ("Salt and pepper", "Olive oil").
    return len(s.split()) <= 5 and not s.rstrip().endswith(".")


def _is_sub_heading(line: str) -> bool:
    """A "For the sauce:" style label inside the ingredient list.

    A main heading is NOT one: "Ingredients:" also ends in a colon, and treating
    it as a sub-heading would name every following row after it.
    """
    s = line.strip()
    if len(s) > _HEADING_MAX or _ING_HEAD.match(s) or _STEP_HEAD.match(s):
        return False
    if not _SUB_HEAD.match(s):
        return False
    # "1 cup sugar:" is not a heading. Anything with an amount in it is an
    # ingredient that happens to be punctuated oddly.
    return units.parse_line(s.rstrip(":"))["qty"] is None


def _is_main_heading(line: str) -> bool:
    s = line.strip()
    return len(s) <= _HEADING_MAX and bool(_ING_HEAD.match(s) or _STEP_HEAD.match(s))


def _resumes_after(rest: list[str]) -> bool:
    """Whether the body visibly carries on, somewhere in ``rest``.

    Two signals, both BORROWED rather than invented, because a second copy of
    either vocabulary is exactly the drift the house rule forbids:

    * an explicit step number — the strongest "this is a step" evidence there
      is, and already parsed by ``_STEP_NUMBER``;
    * a cooking-action cue from ``step_structure``'s phase vocabulary, which is
      the one place in this app that knows Mix/Fold/Chill/Bake/Preheat/Cool.

    Deliberately ANY line rather than the next one: a run-in label introduces
    its own advisory sentence first ("Tip:" / "Use very ripe bananas.") and the
    method resumes on the line after that. Scanning the whole remainder is also
    the generous reading, which is the direction this must err in.
    """
    return any(_STEP_NUMBER.match(ln) or infer_phase(ln) for ln in rest)


def _until_tail(body: list[str]) -> list[str]:
    """``body`` up to the "Notes"/"Tips"/"Comments" heading that ENDS it.

    Everything after a real one belongs to the article, not the recipe: a
    trailing Notes block was importing as extra method steps, and on a copied
    page the comments became steps too.

    The hard part is that the same words appear in two opposite roles, and in
    flat text they look nearly identical:

        …steps…  "Notes"  "Store in an airtight container…"   <- terminator
        …steps…  "Tip:"   "Use very ripe bananas."  "Bake 25 minutes."  <- not

    The rule: a tail heading terminates the body UNLESS it is a run-in label —
    one ending in a colon — and the body visibly resumes after it. A colon is
    what English typography puts on a label that introduces the sentence
    following it ("Tip: use very ripe bananas"); a section heading stands alone
    ("Notes"). Both gates must hold before any content is kept, and the colon
    gate alone is what keeps every existing terminator terminating.

    **This fails SAFE, by construction.** The cut can only ever move LATER than
    the old unconditional one, never earlier, so nothing this used to keep can
    now disappear. When the signal is ambiguous the content stays: a junk
    trailing step is visible in the editor and one click to delete, whereas a
    method that silently lost three of its four steps is neither — which is the
    defect this replaces. Being wrong about "Notes:" therefore costs a stray
    row; being wrong about "Tip:" cost the user their recipe.

    Rejected, with the evidence, so they are not re-tried: the phase vocabulary
    on its OWN does not separate the two cases — "Keep refrigerated for up to
    three days." (a Notes line that must terminate) infers *Chill*, while "Use
    very ripe bananas." (a tip that must not) infers nothing. Narrowing it to
    production phases does separate today's fixtures, but then "Tip: use
    full-fat cream / Chill 4 hours / Serve cold" truncates — it fails UNSAFE,
    which is disqualifying. Used only as the second gate, the full vocabulary
    is safe precisely because any cue at all means "keep".
    """
    out: list[str] = []
    for i, ln in enumerate(body):
        s = ln.strip()
        if len(s) > _HEADING_MAX or not _TAIL_HEAD.match(s):
            out.append(ln)
            continue
        if s.endswith(":") and _resumes_after(body[i + 1:]):
            continue        # a run-in label: drop the label, keep the body
        return out
    return out


def _reads_as_prose(line: str) -> bool:
    """A sentence, i.e. the method has started (or this was never a recipe).

    Deliberately narrow: ends in a full stop AND is long enough to be a clause.
    "Salt and pepper" and "For the sauce:" must not match, or the ingredient list
    ends at the first line without a number in it.
    """
    s = line.strip()
    return s.endswith((".", "!", "?")) and len(s.split()) >= 5


def strip_step_number(line: str) -> str:
    """Drop a leading "1." / "2)" / "Step 3:" prefix. Public because the AI
    completion pass needs the same rule — two copies would drift."""
    return _STEP_NUMBER.sub("", line, count=1).strip()


def _parse_meta(lines: list[str]) -> dict:
    """Servings and prep/cook/total minutes from the lines above the ingredients."""
    meta = {"servings": 0, "prepMinutes": 0, "cookMinutes": 0, "totalMinutes": 0,
            "recipeYield": ""}
    for ln in lines:
        m = _SERVES.search(ln)
        if m and not meta["servings"]:
            meta["servings"] = int(m.group("n") or m.group("n2") or 0)
            meta["recipeYield"] = ln.strip()[:120]
        for key, words in _DURATION_LABEL.items():
            if meta[f"{key}Minutes"]:
                continue
            for w in words:
                # The label and its duration must be adjacent, so "Prep 20 min"
                # matches but a sentence mentioning both words does not.
                hit = re.search(
                    rf"\b{re.escape(w)}\b[^.\n\d]{{0,12}}?(?P<d>\d[\d\s./]*"
                    rf"(?:h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?)"
                    rf"(?:\s*\d+\s*m(?:in(?:ute)?s?)?)?)", ln, re.I)
                if hit:
                    meta[f"{key}Minutes"] = _minutes(hit.group("d"))
                    break
    if not meta["totalMinutes"]:
        meta["totalMinutes"] = meta["prepMinutes"] + meta["cookMinutes"]
    return meta


def _minutes(text: str) -> int:
    """"1 hr 15 min" / "90 minutes" / "1.5 hours" -> whole minutes."""
    total = 0.0
    for num, unit_word in re.findall(r"(\d+(?:[./]\d+)?)\s*([a-z]*)", text.lower()):
        try:
            n = float(num) if "/" not in num else (
                float(num.split("/")[0]) / float(num.split("/")[1]))
        except (ValueError, ZeroDivisionError):
            continue
        if unit_word.startswith("h"):
            total += n * 60
        elif unit_word.startswith("m") or not unit_word:
            total += n
    return int(round(total))


def _payload(*, name, description="", ingredients, steps, meta, notes="") -> dict:
    """The import payload shape.

    Mirrors ``normalize_jsonld``'s keys exactly; ``test_payload_shapes_match``
    asserts that, so the two builders cannot drift apart.
    """
    from .cooking import parse_temperature
    return {
        "name": name or "Imported Recipe",
        "description": description,
        "recipeYield": meta.get("recipeYield", ""),
        "servings": meta.get("servings", 0),
        "prepMinutes": meta.get("prepMinutes", 0),
        "cookMinutes": meta.get("cookMinutes", 0),
        "totalMinutes": meta.get("totalMinutes", 0),
        "cookTemperatureC": parse_temperature(" ".join(s["text"] for s in steps)),
        "ingredients": ingredients,
        "steps": steps,
        "tags": [],
        "imageUrl": "",
        "notes": notes,
    }


# --- Run-on blob segmentation ------------------------------------------------
#
# Some sites put each amount and each name in adjacent inline elements, so a
# browser copy yields ONE line with no separator anywhere:
#
#   "240 gramsEgg Whites(240 g)40 gramsParmesan Cheese finely grated(40 g)…"
#
# Every rule below is about where a LINE ENDS. What a line then means is
# units.parse_line's job, and is deliberately not second-guessed here.
#
# Characters that can begin a quantity. Superscripts and subscripts are included
# because sites render a half as "¹/₂" (three characters) rather than "½", and
# the paste that prompted this contained exactly that. This is the lexical
# question "does a number start here", not the semantic one "what number is it".
_QTY_CHARS = "0-9½⅓⅔¼¾⅕⅛⅜⅝⅞¹²³⁴⁵⁶⁷⁸⁹⁰₀₁₂₃₄₅₆₇₈₉"
_QTY_RUN = rf"[{_QTY_CHARS}](?:[{_QTY_CHARS}]|[./⁄])*"

# THE boundary signal: a quantity glued directly to the end of the previous
# word or to a closing bracket, and followed by a unit word.
#
# The "no space before the number" part is what makes this safe. Prose always
# puts a space in front of a quantity ("bake for 45 minutes", "add 200g flour"),
# so the lookbehind alone excludes almost every legitimate line. The unit-word
# requirement excludes the rest of the known traps: "9x13 pan" ("pan" is not a
# unit), "Grade A2 flour", "7UP", "V8". Traps where the digit is not preceded by
# a letter at all never even reach the unit check: "1 1/2 cups" (the 2 follows a
# slash), "2-3 cloves" (a hyphen), "1.5 kg" (a dot), "180°C" (a degree sign).
#
# The unit candidate is "one capital-or-lowercase letter then lowercase only"
# rather than ``[A-Za-z]+``, because in these blobs the unit is itself glued to
# the name that follows it: the word after the quantity is "gramsParmesan", and
# stopping at the lower→upper transition is what recovers "grams" from it.
_RUNON_QTY = re.compile(
    rf"(?<=[A-Za-z)])(?P<q>{_QTY_RUN})\s*(?P<w>[A-Za-z][a-z]*\.?)")
# Secondary: a closing bracket glued to a capitalised word — "(1 g)Oil". Only
# ever ADDS cuts to a line the quantity rule has already identified as a blob,
# so it can never trigger segmentation on its own.
_RUNON_PAREN = re.compile(r"(?<=\))(?=[A-Z][a-z])")
# A unit glued to the start of the name: "240 gramsEgg Whites". Fixed only at
# the head of an already-segmented line, and only when the lowercase run is a
# UNIT — which is what keeps "McIntosh apples", "iPhone" and "crème fraîche"
# (no transition at all) untouched. A general lower→upper split is far too eager
# to use as a boundary signal; scoped to "the unit I just matched", it is safe.
_RUNON_UNIT_GLUE = re.compile(rf"^(?P<head>{_QTY_RUN}\s*)(?P<u>[a-z]+)(?=[A-Z])")

_RUNON_MIN_CHARS = 40      # a real ingredient line is short; a blob is not
_SEG_MIN_COUNT = 3         # two cuts minimum, so one stray split can't fire
_SEG_MAX_COUNT = 60        # more rows than any recipe has = we read it wrong
_SEG_MAX_CHARS = 200       # one absurdly long segment = we read it wrong
# Words that are grammar, not the name of a food. A segment made only of these
# plus its measurement has no subject, which means the cut landed badly.
_SEG_NOISE = frozenset((
    "or", "and", "of", "the", "a", "an", "to", "plus", "each", "optional",
))
_PAREN_ASIDE = re.compile(r"\([^)]*\)")


def _is_unit_word(word: str) -> bool:
    """Whether ``word`` names a measure, per the one unit vocabulary.

    Singular/plural tolerance is deliberate: this is a *vocabulary lookup* used
    to qualify a boundary, so it must not depend on which spellings the
    measurement parser happens to canonicalise today ("tsps" does not resolve,
    "tsp" does).
    """
    w = (word or "").strip().rstrip(".")
    if not w:
        return False
    return any(units.canonical_unit(c) for c in (w, f"{w}s", w.rstrip("s")))


def _runon_cuts(line: str) -> list[int]:
    """Offsets where a glued quantity starts a new ingredient."""
    return [m.start() for m in _RUNON_QTY.finditer(line)
            if _is_unit_word(m.group("w"))]


def _has_food_words(seg: str) -> bool:
    """Whether a segment names something, not just an amount.

    "40 grams" on its own is a mis-cut, not an ingredient.
    """
    rest = _PAREN_ASIDE.sub(" ", seg)
    return any(w.lower() not in _SEG_NOISE and not _is_unit_word(w)
               for w in re.findall(r"[^\W\d_]{2,}", rest))


def _segments_look_right(segs: list[str]) -> bool:
    """The refusal guard: whether the cuts produced a plausible list.

    A refusal is recoverable — the caller keeps the original text, the existing
    "couldn't parse" path returns None, and the user still has the AI import.
    Silent garbage is not recoverable, so anything doubtful is refused.
    """
    if not _SEG_MIN_COUNT <= len(segs) <= _SEG_MAX_COUNT:
        return False
    if any(len(s) > _SEG_MAX_CHARS for s in segs):
        return False
    return all(_has_food_words(s) for s in segs)


def _split_at(line: str, cuts: list[int]) -> list[str]:
    bounds = [0, *cuts, len(line)]
    return [s for s in (line[a:b].strip()
                        for a, b in zip(bounds, bounds[1:])) if s]  # noqa: B905


def _unglue_unit(seg: str) -> str:
    """"240 gramsEgg Whites" -> "240 grams Egg Whites"."""
    m = _RUNON_UNIT_GLUE.match(seg)
    if m and _is_unit_word(m.group("u")):
        return f"{m.group('head')}{m.group('u')} {seg[m.end():]}"
    return seg


def _segment_ingredient_blob(line: str) -> list[str] | None:
    """A run-on ingredient blob split into lines, or None to leave it alone."""
    if len(line) < _RUNON_MIN_CHARS:
        return None
    cuts = _runon_cuts(line)
    if len(cuts) < _SEG_MIN_COUNT - 1:
        return None
    all_cuts = sorted(set(cuts) | {m.start() for m in _RUNON_PAREN.finditer(line)})
    segs = [_unglue_unit(s) for s in _split_at(line, all_cuts)]
    return segs if _segments_look_right(segs) else None


# The same glued shape happens to a numbered METHOD: "1Mix the flour2Cover and
# rest". The quantity rule cannot see these — "Mix" is not a unit — and a bare
# digit-then-capital is far too eager on its own ("Vitamin B12Supplement").
# What makes it safe is that real step numbers are a RUN: the digits must come
# out 1, 2, 3 … with nothing missing and nothing out of order. Accidental text
# essentially never does that, and a page that numbers its steps always starts
# at 1.
_RUNON_STEP_HEAD = re.compile(r"^(?P<n>\d{1,2})(?=[A-Z])")
_RUNON_STEP = re.compile(r"(?<=[a-z.)])(?P<n>\d{1,2})(?=[A-Z])")
_STEP_MAX_CHARS = 600      # a step is a sentence or two, not a chapter


def _segment_step_blob(line: str) -> list[str] | None:
    """A run-on numbered method split into steps, or None to leave it alone."""
    if len(line) < _RUNON_MIN_CHARS:
        return None
    hits = [*filter(None, [_RUNON_STEP_HEAD.match(line)]),
            *_RUNON_STEP.finditer(line)]
    nums = [int(m.group("n")) for m in hits]
    if len(nums) < 2 or nums != list(range(1, len(nums) + 1)):
        return None
    segs = _split_at(line, [m.start() for m in hits if m.start() > 0])
    if len(segs) != len(nums) or any(len(s) > _STEP_MAX_CHARS for s in segs):
        return None
    # A step is a clause, not a stray number with a capital after it.
    if not all(len(re.findall(r"[^\W\d_]{2,}", s)) >= 2 for s in segs):
        return None
    # Re-punctuate the number rather than dropping it, so the existing
    # strip_step_number rule removes it and the "this line starts a step"
    # signal survives into _step_rows.
    return [re.sub(r"^(\d{1,2})(?=[A-Z])", r"\1. ", s) for s in segs]


def segment_runon_lines(text: str) -> tuple[str, bool]:
    """``text`` with any run-on blob line split, and whether anything was split.

    Applied per line, and only to a line that has no usable structure of its own
    — a normal paste comes back byte-identical, which
    ``test_existing_pastes_are_untouched_by_segmentation`` asserts over every
    fixture in this suite.
    """
    out: list[str] = []
    changed = False
    for ln in text.splitlines():
        stripped = ln.strip()
        segs = _segment_ingredient_blob(stripped) or _segment_step_blob(stripped)
        if segs is None:
            out.append(ln)
            continue
        out.extend(segs)
        changed = True
    return ("\n".join(out), True) if changed else (text, False)


def parse_recipe_text(text: str) -> dict | None:
    """Parse a pasted recipe blob, or None when it isn't confidently a recipe.

    Two strategies, in order:

    * **Headed** — the blob has "Ingredients" / "Method" headings, so the split
      is explicit and sub-headings ("For the sauce:") become ingredient sections.
    * **Unheaded** — no headings at all, so each line is classified. Ingredients
      cluster at the top of a recipe, so the first run of amount-like lines is
      the ingredient list and the prose after it is the method.
    """
    # Same cap as the HTML path. A recipe is not 3 MB, and without this a huge
    # paste built 100,000 ingredient dicts before the endpoint truncated them.
    capped = (text or "")[:12000].replace("\r\n", "\n")
    # Recover the line boundaries the source threw away, or a paste that glued
    # every amount onto the name after it is one line and is rejected outright.
    capped, segmented = segment_runon_lines(capped)
    raw = [ln.strip() for ln in capped.splitlines()]
    lines = [ln for ln in raw if ln]
    if len(lines) < 3:
        return None

    ing_idx = next((i for i, ln in enumerate(lines)
                    if len(ln) <= _HEADING_MAX and _ING_HEAD.match(ln)), None)
    step_idx = next((i for i, ln in enumerate(lines)
                     if len(ln) <= _HEADING_MAX and _STEP_HEAD.match(ln)), None)

    headed = ing_idx is not None or step_idx is not None
    got = _parse_headed(lines, ing_idx, step_idx) if headed else _parse_unheaded(lines)
    if got is None:
        return None
    name, ingredients, steps, head_lines = got
    # ``require_step`` guards the UNHEADED path, where the only evidence is that
    # some lines carry numbers — an article with a few prices in it cleared the
    # old bar. A segmented blob is not that weak a signal: segmentation only
    # fires on three or more amounts glued to the words after them, which is a
    # shape prose cannot produce, so an ingredient list pasted on its own is
    # accepted without a method rather than thrown away.
    if not _enough(ingredients, steps, require_step=not headed and not segmented):
        return None
    if not headed:
        # Guessing, so demand that the list is mostly measurements rather than a
        # couple of stray numbers in an article. A review page with two prices in
        # it otherwise imported as a recipe.
        measured = sum(units.parse_line(r["display"])["qty"] is not None
                       for r in ingredients)
        if measured < 2 or measured < len(ingredients) / 2:
            return None
    return _payload(name=name, ingredients=ingredients, steps=steps,
                    meta=_parse_meta(head_lines))


def _parse_headed(lines, ing_idx, step_idx):
    """Split on explicit headings. Either heading may be absent."""
    first = min(i for i in (ing_idx, step_idx) if i is not None)
    head_lines = lines[:first]
    name = next((ln for ln in head_lines if len(ln) <= 120), "")

    if ing_idx is None:
        # A "Method" heading with no "Ingredients" heading — a very common shape
        # (title, times, the list, then "Method"). Everything above the heading is
        # title + meta + the ingredients, so classify it. Treating it as header
        # and returning no ingredients threw the whole list away.
        head_lines, ing_body = _split_head_from_ingredients(lines[:step_idx])
        name = next((ln for ln in head_lines if len(ln) <= 120), "")
        return name, _ingredient_rows(ing_body), _step_rows(lines[step_idx + 1:]), \
            head_lines
    if step_idx is None:
        ing_body, step_body = lines[ing_idx + 1:], []
    elif ing_idx < step_idx:
        ing_body, step_body = lines[ing_idx + 1:step_idx], lines[step_idx + 1:]
    else:
        # Method printed above the ingredients — rare, but real in older books.
        step_body, ing_body = lines[step_idx + 1:ing_idx], lines[ing_idx + 1:]

    return name, _ingredient_rows(ing_body), _step_rows(step_body), head_lines


def _is_meta_line(line: str) -> bool:
    """A header line that states servings, a time, or a rating — not an amount of
    something. These carry numbers, which is exactly why they were being mistaken
    for the start of the ingredient list ("4.5 stars from 230 reviews")."""
    if _SERVES.search(line):
        return True
    low = line.lower()
    if any(w in low for w in ("star", "rating", "review", "vote", "comment")):
        return True
    return any(re.search(rf"\b{re.escape(w)}\b", low)
               for words in _DURATION_LABEL.values() for w in words)


def _split_head_from_ingredients(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split "title + servings + times" from the ingredient list below it.

    Used when the source labelled its method but not its ingredients. The
    boundary is the start of a RUN of at least two amount-bearing lines that
    aren't metadata — cutting at the first line with a number in it swallowed the
    header, so a rating line became an ingredient and the servings and prep time
    it stated were lost.
    """
    def is_item(ln: str) -> bool:
        return (_looks_like_ingredient(ln)
                and units.parse_line(ln)["qty"] is not None
                and not _is_meta_line(ln))

    for i in range(len(lines) - 1):
        if is_item(lines[i]) and is_item(lines[i + 1]):
            return lines[:i], lines[i:]
    return lines, []


def _parse_unheaded(lines):
    """No headings: classify. Returns None when no ingredient run is found."""
    # Line 1 is the title unless it carries a MEASUREMENT — an unheaded blob
    # often starts straight into the list, and consuming "2 cups flour" as the
    # name both invented a title and deleted an ingredient. Keyed on a quantity
    # rather than _looks_like_ingredient because a short title ("Pancakes") reads
    # as ingredient-ish to that heuristic, and eating it is the worse error.
    has_title = units.parse_line(lines[0])["qty"] is None and len(lines[0]) <= 120
    name = lines[0] if has_title else ""
    body = lines[1:] if has_title else lines
    flags = [_looks_like_ingredient(ln) for ln in body]
    try:
        start = flags.index(True)
    except ValueError:
        return None
    # The run ends at the FIRST line that reads as prose (a sentence), and
    # otherwise tolerates one stray line so a wrapped ingredient or a
    # "For the sauce:" sub-heading doesn't end it. Ending only on two consecutive
    # misses let the run swallow whole documents, because prose alternates short
    # and long lines and the counter never reached two.
    end = len(body)
    misses = 0
    for i in range(start, len(body)):
        if flags[i]:
            misses = 0
            continue
        if _reads_as_prose(body[i]):
            end = i
            break
        misses += 1
        if misses >= 2:
            end = i - 1
            break
    run = body[start:end]
    # With no headings to go on, SOMETHING has to be measurable or this is just
    # prose: "Hello / How are you / See you soon" is three short lines that end
    # without a full stop, which is otherwise indistinguishable from
    # "Salt / Olive oil / Butter". Refusing here sends it to the model, which is
    # the right answer for text we genuinely cannot read.
    if not any(units.parse_line(ln)["qty"] is not None for ln in run):
        return None
    return (name, _ingredient_rows(run), _step_rows(body[end:]),
            [lines[0]] + body[:start])


def _ingredient_rows(body: list[str]) -> list[dict]:
    """Ingredient lines -> rows, carrying any sub-heading as ``section``."""
    rows: list[dict] = []
    section = ""
    for ln in _until_tail(body):
        # Belt and braces: a main heading must never become a row, whichever way
        # it got into the body (a second "Ingredients" further down the page, or
        # a marker injected next to one the page already printed).
        if _is_main_heading(ln):
            continue
        if _is_sub_heading(ln):
            section = ln.rstrip(":").strip()
            continue
        display = units.strip_list_marker(ln).strip()
        if not display:
            continue
        row = {"display": display}
        if section:
            row["section"] = section[:255]
        rows.append(row)
    return rows


def _step_rows(body: list[str]) -> list[dict]:
    """Instruction lines -> steps, with any "1." / "Step 2:" prefix removed.

    Lines are joined when a source hard-wraps mid-sentence: a fragment that does
    not start a new numbered step and does not begin with a capital is a
    continuation, not a step of its own.
    """
    steps: list[dict] = []
    for ln in _until_tail(body):
        if _is_main_heading(ln):
            continue
        numbered = bool(_STEP_NUMBER.match(ln))
        text = strip_step_number(ln)
        if not text:
            continue
        cont = (steps and not numbered and text[:1].islower())
        if cont:
            steps[-1]["text"] = f"{steps[-1]['text']} {text}".strip()
        else:
            steps.append({"title": "", "text": text})
    return steps


def parse_pasted(text: str) -> dict | None:
    """Deterministic read of whatever was pasted, or None to fall through to AI.

    Order matters: microdata beats guessing from prose, and HTML has to become
    line-structured text before the text parser can see the list it contains.
    """
    s = (text or "").strip()
    if not s:
        return None
    if _looks_like_html(s):
        node = extract_microdata_recipe(s)
        if node:
            from .ai.recipe_import import normalize_jsonld
            return normalize_jsonld(node)
        return parse_recipe_text(html_to_lines(s))
    return parse_recipe_text(s)


def _looks_like_html(s: str) -> bool:
    """A tag, not merely an angle bracket — "<1 cup" or "a < b" isn't markup."""
    return bool(re.search(r"<\s*/?\s*[a-zA-Z][a-zA-Z0-9]*[\s/>]", s))
