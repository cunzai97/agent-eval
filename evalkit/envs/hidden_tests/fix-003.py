"""Hidden checks for snapshot fix-003 (text/normalize.py + text/slug.py).

The visible suite calls one function on eight hand-picked strings. Every one of
those strings is *shaped* so that a wrong repair still answers it correctly:
``"Hello___  World"`` collapses to ``hello-world`` whether underscores are turned
into separators or simply deleted (the space supplies the separator anyway),
``"Don't Stop"`` comes out ``dont-stop`` for an implementation that deletes the
apostrophe and for one that deleted only the ASCII quote from a hard-coded list,
and ``"alpha beta gamma"`` at ``max_length=12`` is a cut *inside* a word, so code
that always drops the last hyphen-separated piece looks right there too. Nothing
in the visible suite ever calls ``normalize_text``, so normalization can stay a
``.lower()`` stub as long as ``slugify`` does the work itself.

Each case below kills one of the repairs the visible suite cannot tell apart.
Every expected value was produced by running an implementation, not by reasoning
about the character tables.

Deliberate absences -- the prompt does not settle these, so pinning either side
would move the goalposts rather than catch a defect:
- a cut that lands exactly on a word boundary (``"alpha-beta-gamma"``,
  ``max_length=10``): returning ``alpha-beta`` and returning ``alpha`` both obey
  every stated rule, and the historical population splits 6/21 on it. Only the
  case where the naive cut leaves a *dangling hyphen* (``max_length=11``) is
  pinned, because stripping the forbidden separator is the stated requirement
  and dropping a whole word along with it is not what any rule asks for.
- ``max_length`` too small to hold a single whole word: ``ValueError``, an empty
  string and a truncated first word are all defensible readings of the prompt.
- letters outside Latin-1 (``α``, ``привет``, ``中文``): the population splits
  23/5 between "keep any Unicode letter or digit" and "keep ASCII alphanumerics",
  and the task prompt never says which. Neither is required here.
- quote-like marks other than the apostrophe (``‘`` U+2018, ``` `` U+0060): the
  population splits 16/12 on deleting versus separating them.  The separator
  sweep below therefore walks every punctuation and space shape *except* the
  quote family.
- the order of NFKD versus casefold.  ``U+0345`` (combining Greek ypogegrammeni)
  is the trap: casefold turns it into the *letter* ι, so whether it is stripped
  as a mark or folded into the word depends on which operation ran first.  It is
  not in the combining-mark sweep for that reason; ``Ⅻ``/``Ⅿ`` are, because both
  orders land on ``xii``/``mxi``.

Sweep versus sample: the sampled cases above pin single points, and a point can
be memorised.  What cannot is a property that has to hold for all 56 separator
characters, all 12 combining marks, all 16 cut lengths of 6 texts, and all 16
rejected object types -- that part of the module is what catches the repair that
only knows the three separators the visible suite happens to show.
"""
import re
import unicodedata

import pytest

from text.normalize import normalize_text
from text.slug import slugify

# A finished slug: lowercase alphanumeric runs joined by single hyphens.
SLUG_SHAPE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")

# Normalization duties the prompt assigns to the normalize layer, on characters
# the visible suite never shows.
NORMALIZES_TO = [
    ("Crème", "creme"),          # precomposed accent -> base letter
    ("Cre\u0300me", "creme"),    # already-decomposed input loses the mark
    ("naïve", "naive"),
    ("Straße", "strasse"),       # casefold, not lower()
    ("ẞ", "ss"),                 # capital sharp s: lower() cannot do this
    ("ﬁle", "file"),             # compatibility decomposition, not an accent
    ("ﬄ", "ffl"),
    ("Ⅻ", "xii"),                # roman numeral folds to its letters
    ("①", "1"),
    ("Ｄ", "d"),                  # fullwidth form
]


def test_the_normalize_layer_does_its_own_normalization():
    """Kills "facade" repairs: a NOTE comment added to normalize.py while
    normalize_text still returns ``value.lower()`` and slugify secretly does the
    work. The task prompt says the defect may live in either layer, and the
    visible suite never calls this function, so only a direct probe can tell a
    repaired layer from a stub. ``files_changed`` cannot: a comment edit counts."""
    for source, wanted in NORMALIZES_TO:
        assert normalize_text(source) == wanted, f"normalize_text({source!r}) must fold to {wanted!r}"


@pytest.mark.parametrize("value", [None, 5, 3.2, b"x", ["a"], {"a": 1}])
def test_the_normalize_layer_rejects_non_strings_itself(value):
    """Kills the same facade from the other side: ``value.lower()`` on a non-string
    raises AttributeError, which is neither the promised TypeError nor a refusal."""
    with pytest.raises(TypeError):
        normalize_text(value)


@pytest.mark.parametrize("source, wanted", [
    ("naïve café résumé", "naive-cafe-resume"),
    ("Crème BRÛLÉE", "creme-brulee"),      # casefold has to run over every char
    ("ﬁle ﬄower", "file-fflower"),
    ("№ one", "no-one"),
    ("Ǳungla", "dzungla"),
    ("½ cup", "1-2-cup"),
])
def test_folding_is_decomposition_driven_not_a_table_of_pinned_strings(source, wanted):
    """Kills repairs that carry a hand-written per-character table for the letters
    the visible tests happen to use (é è û ß). NFKD and casefold are named in the
    prompt, so a table that cannot reach Ⅻ/ﬄ/Ǳ/½ is the visible assertion, not the
    rule, being satisfied."""
    assert slugify(source) == wanted


@pytest.mark.parametrize("source, wanted", [
    ("a.b", "a-b"),
    ("a/b/c", "a-b-c"),
    ("foo_bar-baz qux.dot", "foo-bar-baz-qux-dot"),
    ('say "hi" now', "say-hi-now"),
    ("a@b.com x", "a-b-com-x"),
    ("na¼", "na1-4"),
    ("x²+1", "x2-1"),
])
def test_punctuation_between_words_separates_them_instead_of_vanishing(source, wanted):
    """Kills the defect that is still live in the shipped slug layer: punctuation is
    deleted, so its neighbours fuse ("a.b" -> "ab"). The visible suite cannot see
    this, because its one separator test puts the punctuation next to a space.
    Merging separators (题面: 合并分隔符) means a separator survives the merge."""
    assert slugify(source) == wanted


@pytest.mark.parametrize("source, wanted", [
    ("a  -- __ .. b", "a-b"),
    ("__a--b__", "a-b"),
    ("a----b", "a-b"),
    ("a \t b", "a-b"),
    ("a \U0001F389 b", "a-b"),
])
def test_a_run_of_mixed_separators_becomes_exactly_one_hyphen(source, wanted):
    """Kills an ordering slip in the merge: hyphen-izing whitespace first and only
    then deleting other punctuation leaves "a--b" / "a---b" between words, and
    deleting instead of replacing leaves "ab". Both stay green on "Hello___  World".
    """
    assert slugify(source) == wanted


@pytest.mark.parametrize("source, wanted", [
    ("L'Ordinateur", "lordinateur"),
    ("Don't Stop Me", "dont-stop-me"),
    ("d\u2019un ami", "dun-ami"),
    ("rock\u2019n\u2019roll", "rocknroll"),
])
def test_apostrophes_disappear_without_splitting_the_word(source, wanted):
    """Kills two things: a hard-coded fix for the literal "Don't Stop", and an
    apostrophe that was deleted for ASCII only, so the typographic apostrophe
    U+2019 keeps splitting "d’un" into "d-un". The prompt asks for apostrophes to
    be removed outright; the visible test's own name says removal must not split."""
    assert slugify(source) == wanted


@pytest.mark.parametrize("source, limit, wanted", [
    ("alpha-beta-gamma", 8, "alpha"),
    ("alpha-beta-gamma", 9, "alpha"),
    ("alpha-beta-gamma", 11, "alpha-beta"),
    ("alpha-beta-gamma", 12, "alpha-beta"),
    ("alpha-beta-gamma", 14, "alpha-beta"),
    ("alpha-beta-gamma", 16, "alpha-beta-gamma"),
    ("alpha-beta-gamma", 20, "alpha-beta-gamma"),
    ("alpha beta gamma", 11, "alpha-beta"),
    ("alpha beta gamma", 13, "alpha-beta"),
])
def test_max_length_drops_only_the_offending_tail(source, limit, wanted):
    """Kills truncation that reads its own cut wrongly. ``"alpha-beta-gamma"`` at
    ``max_length=11`` is ``"alpha-beta-"``: the forbidden thing is the trailing
    hyphen, so the answer is that hyphen removed -- 20 of the 28 historical repairs
    get this right, and 8 return "alpha" because they cut at the last hyphen
    unconditionally and throw away a complete word that still fits."""
    result = slugify(source, max_length=limit)
    assert result == wanted
    assert len(result) <= limit
    assert not result.startswith("-") and not result.endswith("-")


def test_the_default_cap_still_limits_the_result():
    """Kills a repair that stops paying attention to max_length: the default is 80,
    a 150-character title has to come back inside it, and it still has to end on a
    word boundary with no dangling separator."""
    long_title = "alpha " * 20 + "omega"
    result = slugify(long_title)
    assert len(result) <= 80, f"default cap of 80 ignored: {len(result)} characters"
    assert result == "-".join(["alpha"] * 13), f"13 whole words fit in 80, got {result!r}"
    assert SLUG_SHAPE.match(result)


@pytest.mark.parametrize("source", ["", "   ", "---", "...  !!!", "\u0301\u0302", "'", "\u2019", "\t\n"])
def test_a_result_with_nothing_left_in_it_is_rejected(source):
    """Kills repairs that quietly return "" for content that normalizes away: the
    prompt refuses an empty result, and returning "" is what lets a caller publish
    an empty URL. Combining marks alone must also count as nothing left."""
    with pytest.raises(ValueError):
        slugify(source)


@pytest.mark.parametrize("value", [None, 5, 3.2, b"x", ["a"], {"a": 1}])
def test_the_public_entry_rejects_non_strings(value):
    """TypeError, not AttributeError from a bare .lower() and not ValueError."""
    with pytest.raises(TypeError):
        slugify(value)


@pytest.mark.parametrize("source", [
    "Crème Brûlée", " -- Alpha -- ", "Don't Stop", "Straße", "Hello___  World",
    "foo_bar-baz qux.dot", "a@b.com x", "Ⅻ ﬄow ½",
])
def test_a_slug_is_a_fixpoint_of_the_pipeline(source):
    """Kills repairs whose output is not itself a legal slug: feeding the result
    back in must not change it. Leftover separators, doubled hyphens and dropped
    punctuation that fused words all break here, including where a single-shot
    expected string would have looked fine."""
    once = slugify(source)
    assert SLUG_SHAPE.match(once), f"{source!r} produced a malformed slug {once!r}"
    assert slugify(once) == once, f"{source!r}: {once!r} is not stable, it becomes {slugify(once)!r}"


# --------------------------------------------------------------------------
# Sweeps over the same shortcut space the sampled cases above probe.  A single
# pinned point can be memorised; a property that has to hold for every
# separator character, every cut length and every rejected type cannot.
#
# `reference_slug` is the measuring stick, not a second opinion: it is first
# checked against the visible suite's own expectations, and it is only applied
# to inputs whose every character folds to the same answer under both readings
# the historical population splits on (ASCII-only vs Unicode alphanumerics) --
# no Greek/CJK, no ø, no µ.
# --------------------------------------------------------------------------

APOSTROPHES = "'\u02bc\u02bb‘’‛\uff07"
ASCII_TOKENS = re.compile(r"[0-9A-Za-z]+")


def reference_fold(text):
    out = unicodedata.normalize("NFKD", text).casefold()
    return "".join(ch for ch in out if not unicodedata.combining(ch))


def reference_slug(value):
    text = reference_fold(value)
    for ch in APOSTROPHES:
        text = text.replace(ch, "")
    return "-".join(ASCII_TOKENS.findall(text))


VISIBLE_EXPECTATIONS = [
    ("Crème Brûlée", "creme-brulee"),
    ("Straße", "strasse"),
    ("Hello___  World", "hello-world"),
    ("Don't Stop", "dont-stop"),
    (" -- Alpha -- ", "alpha"),
]


def test_the_measuring_stick_reproduces_the_visible_expectations():
    for source, wanted in VISIBLE_EXPECTATIONS:
        assert reference_slug(source) == wanted, (source, reference_slug(source), wanted)


UNSEEN_SHAPES = [
    "Grüße aus München", "Señor García", "À É È Ù Ô î", " ﬄusion of ﬅyle ",
    "Köln Düsseldorf", "François naïveté", "e\u0301clair", "x＝y", "ßß ß",
    "ﬁﬁ ﬆep", "ＦＵＬＬＷＩＤＴＨ", "M\u0302O\u0308T\u0301E", "Ⅻ ⅯⅩⅠ",
    "café 2026!", "Crème\nBrûlée", " résumé — naïve ", "ǅungla_ǆungla",
]


@pytest.mark.parametrize("source", sorted(UNSEEN_SHAPES))
def test_shapes_the_visible_suite_never_shows(source):
    """折叠 + 删撇号 + 合并分隔符三件事在没见过的输入上仍然同时成立。"""
    assert reference_slug(source), source
    assert slugify(source) == reference_slug(source), reference_slug(source)


# 每个非字母数字字符都必须*成为*分隔符：可见套件只演示了空格、下划线、连字符三种，
# 所以"把标点删掉"和"只认这三种"都能全绿，而 a/b 与 ab 撞成同一个 slug 正是这个
# 契约最不能接受的后果（题面：合并分隔符 = 合并，不是丢弃）。
# 引号族（' " ‘ ’ “ ” ` « » ‛ ʼ）不在名单里：它们要么被题面点名删除（撇号），要么
# 历史人群在"删除还是分隔"上 16/12 分裂，任何一边都不该被钉死。
SEPARATOR_CHARS = [
    " ", "\t", "\n", "\r", "\x0b", "\x0c", "\xa0", "\u2000", "\u2007", "\u2009",
    "\u202f", "\u3000", "/", "\\", "|", ".", ":", ";", ",", "!", "?", "@", "#",
    "$", "%", "^", "&", "*", "+", "=", "<", ">", "~", "(", ")", "[", "]",
    "{", "}", "\u2013", "\u2014", "\u2026", "\u2044", "\u00a7", "\u00b6",
    "\u2022", "\u00a1", "\u00bf", "\u2020",
    "−", "｡", "．", "･", "！", "：", "／",
]


@pytest.mark.parametrize("sep", SEPARATOR_CHARS)
def test_a_separator_never_fuses_two_words(sep):
    assert slugify("a" + sep + "b") == "a-b"


@pytest.mark.parametrize("sep", SEPARATOR_CHARS)
def test_a_run_of_the_same_separator_still_leaves_one_hyphen(sep):
    assert slugify("a" + sep * 3 + "b") == "a-b"


FUSION_PAIRS = [("a/b", "ab"), ("a.b", "ab"), ("a\tb", "ab"), ("a+b", "ab"),
                ("one.two", "onetwo"), ("x=y", "xy"), ("foo.bar.baz", "foobarbaz")]


@pytest.mark.parametrize("separated,fused", FUSION_PAIRS)
def test_two_inputs_that_differ_only_by_a_separator_cannot_share_a_slug(separated, fused):
    """撞 slug 是可见套件结构上看不见的缺陷：把分隔符删掉以后两个不同标题落成同一个
    URL，而 8 条可见断言依然全绿。"""
    assert slugify(separated) != slugify(fused)


# U+0345 不在名单里：casefold 会把它折叠成字母 ι，于是它既不再是"要去掉的音标"，
# 也不再是 Mn——那考的是题面没规定的 NFKD/casefold 先后次序。
COMBINING_MARKS = ["\u0301", "\u0300", "\u0308", "\u0327", "\u0323", "\u0302",
                   "\u030b", "\u031c", "\u0483", "\u0306", "\u030c", "\u0325"]


@pytest.mark.parametrize("mark", COMBINING_MARKS)
def test_a_combining_mark_is_stripped_rather_than_used_as_a_separator(mark):
    """题面把"去除组合音标"和"合并分隔符"分成两件事：把音标当分隔符会凭空造出词边界。"""
    assert slugify("a" + mark + "b") == "ab"


TRUNCATION_TEXTS = [
    "alpha beta gamma", "one two three four", "a bb ccc dddd",
    "supercalifragilistic expialidocious", "x y z w", "alpha-beta-gamma",
]
TRUNCATION_LENGTHS = [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 15, 17, 19, 24]


def token_prefixes(slug):
    tokens = slug.split("-")
    return {"-".join(tokens[:i]) for i in range(len(tokens) + 1)}


@pytest.mark.parametrize("source", TRUNCATION_TEXTS)
@pytest.mark.parametrize("limit", TRUNCATION_LENGTHS)
def test_truncation_only_ever_cuts_between_whole_words(source, limit):
    """题面明说 max_length 不能留下半个尾部单词或连字符。整词前缀、不超长、不返回
    空串，这三条在每个长度上都要成立，而不是只在可见套件采样的那一个长度上。
    第一个整词都装不下的长度沿用本模块开头的弃权口径（拒绝/空串/截断首词都算可读
    的题面读法），只检查"不得超过上限"。"""
    full = slugify(source)
    tokens = full.split("-")
    if len(tokens[0]) > limit:
        try:
            got = slugify(source, max_length=limit)
        except ValueError:
            return
        assert len(got) <= limit, f"{source!r}@{limit}: {len(got)} > cap"
        return
    got = slugify(source, max_length=limit)
    assert got in token_prefixes(full), \
        f"{source!r}@{limit}: {got!r} is not a whole-word prefix of {full!r}"
    assert len(got) <= limit, f"{source!r}@{limit}: {len(got)} > cap"
    assert got, f"{source!r}@{limit}: an empty slug was handed back instead of refused"


def test_a_call_leaves_no_trace_for_the_next_call():
    """带 max_length 的调用不得影响后续调用；同一输入重复调用必须逐字节一致。"""
    source = "Crème Brûlée 2026"
    once = slugify(source)
    for _ in range(3):
        assert slugify(source) == once
        assert slugify(source, max_length=5) == slugify(source, max_length=5)
    assert slugify(source) == once
    assert slugify("Don't Stop") == "dont-stop"


@pytest.mark.parametrize("value", [True, False, 0, 3j, (), set(), frozenset(["a"]),
                                   object(), Ellipsis, range(3)])
def test_non_strings_beyond_none_are_refused_with_typeerror(value):
    """可见套件只试过 None，所以 `if value is None: raise TypeError` 与
    `hasattr(value, "casefold")` 这类鸭子检查能一路全绿。bool 是最典型的漏网之鱼
    （isinstance(True, int) 为真），0/False 则会被"假值即空输入"误判成 ValueError。"""
    with pytest.raises(TypeError):
        slugify(value)
