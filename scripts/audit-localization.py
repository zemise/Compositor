#!/usr/bin/env python3
"""Audit the Simplified Chinese localization.

Standalone and self-contained: this is deliberately NOT wired into CI. Run it by hand, most
usefully just after pulling a new upstream main, to see (a) what the catalog is missing and
(b) which display-call literals will not localize because no catalog key matches them.

    python3 scripts/audit-localization.py

Exit status is 0 when nothing is outstanding, 1 when a user-visible string is still unlocalized,
so it can double as a check without being part of the build.
"""

import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(REPO, "Compositor", "Localizable.xcstrings")
SOURCE = os.path.join(REPO, "Compositor")
TARGET = "zh-Hans"
SOURCE_LANG = "en"

# Calls whose arguments are shown to the user. Every string literal in one of these argument
# lists is a localizable key: a ternary of literals and an interpolated literal both count.
DISPLAY_CALL_PATTERNS = [
    r"\bText\(", r"\bButton\(", r"\bLabel\(", r"\bTextField\(", r"\bToggle\(", r"\bPicker\(",
    r"\bSection\(", r"\bMenu\(", r"\bLink\(", r"\bNavigationLink\(", r"\bStepper\(",
    r"\bNSMenuItem\(title:", r"\bNSMenu\(title:", r"\bNSTextField\(labelWithString:",
    r"\.help\(", r"\.accessibilityLabel\(", r"\.accessibilityHint\(", r"\.navigationTitle\(",
    r"\.setAccessibilityLabel\(", r"\bLocalizedStringKey\(",
    # AppKit text-setting calls.
    r"\.addItem\(withTitle:", r"\.addButton\(withTitle:",
    # Explicit lookups: their literal argument is a key, so one missing here is a real gap too.
    r"\.localized\(", r"\blocalizedString\(", r"\blocalizedFormat\(", r"String\(localized:",
]
DISPLAY_RE = re.compile("|".join(DISPLAY_CALL_PATTERNS))

# AppKit properties assigned a displayed string: the right-hand side is scanned to the end of the
# statement, since a ternary here spans lines and a plain String never localizes on its own.
DISPLAY_ASSIGN = re.compile(r"\.(?:toolTip|messageText|informativeText|accessibilityDescription|title|stringValue)\s*=\s*(?![=])")
# Right-hand sides that are deliberately not text (an identifier, a setter chain, …).
ASSIGN_SKIP = re.compile(r"^\s*(?:nil|true|false|\.|\.zero|\.init|NSColor|NSCursor|#selector)")

# Labeled arguments whose value is never shown as text. Their literals are not keys.
NON_TEXT_LABELS = {
    "systemImage", "image", "id", "tag", "keyEquivalent", "placement", "format", "contentType",
    "anchor", "action", "of", "in", "value", "text", "isOn", "selection", "role", "style",
    "coordinateSpace", "scale", "count", "content", "tint", "color", "width", "height",
    "minWidth", "maxWidth", "alignment", "spacing", "from", "to", "at", "size", "range", "unit",
    "precision", "decimals", "track", "sensitivity", "disabled", "options", "allowedContentTypes",
    "isPresented", "binding", "coordinateSpace2", "verbatim", "systemName", "tint2",
}

# A translated string fed back in as a lookup key (localized text inside a key).
DOUBLE = re.compile(r"LocalizedStringKey\(\s*(?!\")[^)]*localized"
                    r"|(?:localized|localizedString|localizedFormat)\(\s*[^,)\"']*localized")
CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
FORMAT_SPEC = re.compile(r"%(?:\d+\$)?[-+#0]*[0-9]*(?:\.[0-9]+)?(?:hh|h|ll|l|z|j|t|q|L)?[a-zA-Z@]")

# Interpolations are normalized to this marker on both sides before comparing keys.
SPEC_MARK = "\x01"

# --- Non-translatable literals: excluded from the "still unlocalized" list by category. ---

# Keyboard glyphs and menu punctuation (⌘ ⌥ ⌃ ⇧ ⌫ ⏎ • and friends). Not the middle dot,
# which is a plain separator inside sentences like "Drag to pan · Pinch to zoom".
GLYPHS = set("⌘⌥⌃⇧⌫⌦⌤⏎↩⇥⎋⌅⇪⇞⇟↖↗↘↙←→↑↓⇡⇣⇠⇢•␣⏏⌧")
# Brand and product names, file formats, color spaces, and technology tokens.
BRANDS = {
    "Compositor", "Adobe", "Photoshop", "macOS", "Apple", "iOS", "iPadOS",
    "PSD", "PSB", "JPEG", "JPG", "HEIC", "TIFF", "SVG", "GIF", "PNG", "WebP", "EXR",
    "Camera Raw", "sRGB", "P3", "Display P3", "LUT", "CMYK", "Metal", "GPU", "CPU", "PDF",
    "Studio Display", "Retina",
}
# File extensions and UTType-ish identifiers.
EXTENSIONS = {"png", "jpg", "jpeg", "comp", "psd", "psb", "heic", "tiff", "tif", "svg", "gif", "webp"}
# Spelled-out identifiers: camelCase / snake_case tokens that are names, not prose.
IDENTIFIER = re.compile(r"^(?:[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*|[a-z][a-z0-9]*_[a-z0-9_]+)$")
# Dotted identifiers: SF Symbol names, UTType identifiers, Bundle ids, UserDefaults keys.
DOTTED = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9_+-]+)+$")
# A literal that is only punctuation, symbols, digits, or format specifiers.
TRIVIAL = re.compile(r"^[\s\d\W]*$")


def load_catalog():
    with open(CATALOG, encoding="utf-8") as handle:
        return json.load(handle)


def unit_value(entry, language):
    """The stringUnit value for one language, or None."""
    localizations = entry.get("localizations") or {}
    unit = (localizations.get(language) or {}).get("stringUnit") or {}
    return unit.get("value")


def normalize_key(key):
    """Replace every format specifier with one marker, so %@ and %lld compare equal."""
    return FORMAT_SPEC.sub(SPEC_MARK, key)


def swift_files():
    for root, _dirs, names in os.walk(SOURCE):
        for name in names:
            if name.endswith(".swift"):
                yield os.path.join(root, name)


def scan_literals(text):
    """Every Swift string literal in `text` as dicts of body/span/interpolation flag."""
    out = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != '"':
            i += 1
            continue
        if text.startswith('"""', i):
            end = text.find('"""', i + 3)
            if end == -1:
                break
            out.append({"body": text[i + 3:end], "start": i, "end": end + 3,
                        "interpolated": False, "multiline": True})
            i = end + 3
            continue
        buffer, interpolated, j = [], False, i + 1
        while j < n:
            ch = text[j]
            if ch == "\\" and j + 1 < n:
                if text[j + 1] == "(":
                    depth, k = 0, j + 1
                    while k < n:
                        if text[k] == "(":
                            depth += 1
                        elif text[k] == ")":
                            depth -= 1
                            if depth == 0:
                                break
                        k += 1
                    buffer.append(SPEC_MARK)
                    interpolated = True
                    j = k + 1
                    continue
                escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "'": "'", "0": "\0"}
                buffer.append(escapes.get(text[j + 1], text[j + 1]))
                j += 2
                continue
            if ch == '"':
                j += 1
                break
            buffer.append(ch)
            j += 1
        out.append({"body": "".join(buffer), "start": i, "end": j,
                    "interpolated": interpolated, "multiline": False})
        i = j
    return out


def argument_spans(text, literals):
    """Yield (call, argument_text, argument_start) for every display-call argument list."""
    starts = {literal["start"]: literal for literal in literals}

    def close_of(open_index):
        depth, i, n = 0, open_index, len(text)
        while i < n:
            literal = starts.get(i)
            if literal:
                i = literal["end"]
                continue
            ch = text[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return -1

    for match in DISPLAY_RE.finditer(text):
        open_index = text.find("(", match.start())
        if open_index == -1:
            continue
        close_index = close_of(open_index)
        if close_index == -1:
            continue
        # Top-level arguments inside the parens.
        depth, start, i = 0, open_index + 1, open_index + 1
        while i < close_index:
            literal = starts.get(i)
            if literal:
                i = literal["end"]
                continue
            ch = text[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 0:
                yield text[match.start():match.end()], start, i
                start = i + 1
            i += 1
        yield text[match.start():match.end()], start, close_index

    # AppKit property assignments: scan the right-hand side to the end of the statement.
    for match in DISPLAY_ASSIGN.finditer(text):
        rhs = match.end()
        if ASSIGN_SKIP.match(text[rhs:rhs + 24]):
            continue
        yield match.group(0), rhs, statement_end(text, rhs, starts)


def label_of(argument):
    match = re.match(r"\s*([A-Za-z_]\w*)\s*:\s", argument)
    return match.group(1) if match else None


def statement_end(text, index, starts):
    """End of the expression beginning at `index`: the first newline at nesting depth zero."""
    depth, i, n = 0, index, len(text)
    while i < n:
        literal = starts.get(i)
        if literal:
            i = literal["end"]
            continue
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "\n" and depth <= 0:
            return i
        i += 1
    return n


def adjacent_to_concatention(text, literal):
    """True when `+` sits just outside the literal: it is one operand of a concatenation."""
    before = text[:literal["start"]].rstrip()
    after = text[literal["end"]:].lstrip()
    return before.endswith("+") or after.startswith("+")


def literal_key(literal):
    """The catalog key an extracted literal would need."""
    return literal["body"]


def is_translatable(body):
    """False for the categories that must never reach the catalog."""
    stripped = body.strip()
    if not stripped:
        return False
    # A bare shortcut token ("⌘N", "⌘⇧Z") is a key equivalent, not copy. A sentence that merely
    # mentions one ("New canvas (⌘N) · Drop images here for new tabs") is copy and does need a
    # key — excluding it wholesale was how a missing hint string slipped past this audit.
    if any(character in GLYPHS for character in stripped) and not any(c.isspace() for c in stripped):
        return False
    if stripped in BRANDS or stripped.lower() in EXTENSIONS:
        return False
    if len(stripped) == 1 and stripped.isascii() and stripped.isupper():
        return False  # a color-channel letter (R, G, B), the same in every language
    if DOTTED.match(stripped) and " " not in stripped:
        return False
    if IDENTIFIER.match(stripped) and " " not in stripped:
        return False
    if TRIVIAL.match(stripped):
        return False
    return True


def audit_catalog(strings):
    translated, missing, no_english = [], [], []
    for key, entry in strings.items():
        if not unit_value(entry, SOURCE_LANG):
            no_english.append(key)
        (translated if unit_value(entry, TARGET) else missing).append(key)
    return translated, missing, no_english


def audit_formats(strings):
    """Keys whose en and zh-Hans format specifiers differ: a %@ vs %lld swap renders wrong."""
    problems = []
    for key, entry in strings.items():
        source = unit_value(entry, SOURCE_LANG)
        target = unit_value(entry, TARGET)
        if not source or not target:
            continue
        # Positional specifiers (%1$@) let a translation reorder words; compare the specifiers
        # themselves, ignoring their position numbers.
        strip = lambda specs: sorted(spec.replace("$", "").lstrip("%0123456789") for spec in specs)
        if strip(FORMAT_SPEC.findall(source)) != strip(FORMAT_SPEC.findall(target)):
            problems.append((key, source, target))
    return problems


def audit_display_literals(strings):
    """Every display-call literal whose normalized key is absent from the catalog."""
    catalog = {normalize_key(key) for key in strings}
    candidates = []
    for path in sorted(swift_files()):
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        literals = scan_literals(text)
        for call, arg_start, arg_end in argument_spans(text, literals):
            argument = text[arg_start:arg_end]
            if "String(format:" in argument:
                continue  # a printf template, not a key
            label = label_of(argument)
            if label and label in NON_TEXT_LABELS:
                continue
            for literal in literals:
                if literal["start"] < arg_start or literal["end"] > arg_end:
                    continue
                if literal["multiline"]:
                    continue  # """…""" is Metal shader source in this repo, never UI copy
                body = literal["body"]
                if adjacent_to_concatention(text, literal):
                    continue  # a fragment of a `+` concatenation is not a key on its own
                if normalize_key(body) in catalog:
                    continue
                if not is_translatable(body):
                    continue
                line = text.count("\n", 0, literal["start"]) + 1
                candidates.append((os.path.relpath(path, REPO), line, call, body))
    # De-duplicate: the same literal can sit inside Text( … ) and LocalizedStringKey( … ).
    seen, unique = set(), []
    for row in candidates:
        identity = (row[0], row[1], row[3])
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(row)
    return unique


# --- Helper-passed display strings. -----------------------------------------------------
#
# A helper such as `control(_ title: String, …)` that builds `Text(LocalizedStringKey(title))`
# turns every literal its callers pass for `title` into a catalog key. Direct display-call scanning
# never sees those literals, because they are arguments to the helper, not to `Text`. A parameter
# counts as localized when it is used as an explicit key — `LocalizedStringKey(p)`,
# `localizedString(p)`, `localizedFormat(p)`, `.localized(p)` — or, when the parameter's own type is
# `LocalizedStringKey`, through `Text(p)`, `Label(p)`, `.help(p)`, `.accessibilityLabel(p)`; or when
# it is passed onward to another such helper. The same holds for a `String` stored property of a
# view struct (CameraRawSlider's `help`, TransformValueField's `label`) and its memberwise-init
# labels.

KEY_POSITIONS = [
    r"\bLocalizedStringKey\(\s*%s\b",
    r"\blocalizedString\(\s*%s\b",
    r"\blocalizedFormat\(\s*%s\b",
    r"\.localized\(\s*%s\b",
]
VIEW_POSITIONS = [
    r"\bText(?:Field)?\(\s*%s\b",
    r"\bLabel\(\s*%s\b",
    r"\.help\(\s*%s\b",
    r"\.accessibilityLabel\(\s*%s\b",
]
STRING_TYPE = re.compile(r"^\s*(?:\w+\.)?(?:String|LocalizedStringKey)\s*\??\s*$")
KEY_TYPE = re.compile(r"^\s*(?:\w+\.)?LocalizedStringKey\s*\??\s*$")
FUNC_DECL = re.compile(r"\bfunc\s+([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*\(")
STRUCT_DECL = re.compile(r"\bstruct\s+([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*[:{]")
STRING_PROP = re.compile(r"(?:var|let)\s+(\w+)\s*:\s*((?:String|LocalizedStringKey)\??)\s*(?:=|\n|$)")


def source_without_literals(text, literals):
    """`text` with every literal and comment blanked, so brackets can be matched structurally."""
    chars = list(text)
    for literal in literals:
        for index in range(literal["start"], literal["end"]):
            if chars[index] != "\n":
                chars[index] = " "
    for pattern in (r"//[^\n]*", r"/\*.*?\*/"):
        for match in re.finditer(pattern, text, re.S):
            for index in range(match.start(), match.end()):
                if chars[index] != "\n":
                    chars[index] = " "
    return "".join(chars)


def closing_index(masked, open_index):
    """The index of the bracket matching the one at `open_index`, or -1."""
    depth, index, size = 0, open_index, len(masked)
    while index < size:
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def split_top_level(masked, start, end):
    """[start, end) split on top-level commas, as (start, end) pairs."""
    parts, depth, begin, index = [], 0, start, start
    while index < end:
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append((begin, index))
            begin = index + 1
        index += 1
    parts.append((begin, end))
    return parts


def declared_parameters(masked, open_index, close_index):
    """(external label, name, type, span start) for each parameter of a declaration's paren list."""
    out = []
    if not masked[open_index + 1:close_index].strip():
        return out
    for start, end in split_top_level(masked, open_index + 1, close_index):
        depth, colon = 0, -1
        for index in range(start, end):
            char = masked[index]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif char == ":" and depth == 0:
                colon = index
                break
        if colon == -1:
            continue
        tokens = masked[start:colon].strip().split()
        if len(tokens) == 1:
            external, name = tokens[0], tokens[0]
        elif len(tokens) >= 2:
            external, name = tokens[0], tokens[1]
        else:
            continue
        kind = masked[colon + 1:end].split("=")[0].strip()
        out.append((external, name, kind, start))
    return out


def localized_tags(body, name, kind):
    """How `name` reaches a displayed string inside `body`, given its declared type."""
    pattern = re.escape(name)
    tags = [tag for tag, text in zip(("key", "localizedString", "localizedFormat", ".localized"),
                                     KEY_POSITIONS) if re.search(text % pattern, body)]
    if KEY_TYPE.match(kind):
        tags += [tag for tag, text in zip(("Text", "Label", ".help", ".a11y"), VIEW_POSITIONS)
                 if re.search(text % pattern, body)]
    return tags


def helper_localized_keys(files, texts, masks):
    """Map each helper name to the argument keys ('pos' index or 'label' name) it localizes."""
    func_keys, func_decls, struct_keys = {}, {}, {}
    for path in files:
        masked, body_of = masks[path], texts[path]
        for match in FUNC_DECL.finditer(masked):
            name = match.group(1)
            paren = masked.find("(", match.start())
            close = closing_index(masked, paren)
            if close == -1:
                continue
            brace = masked.find("{", close)
            if brace == -1:
                continue
            body_end = closing_index(masked, brace)
            if body_end == -1:
                continue
            params = declared_parameters(masked, paren, close)
            if not params:
                continue
            body = body_of[brace:body_end]
            func_decls.setdefault(name, []).append((params, body))
            keys = set()
            for external, param, kind, start in params:
                if not STRING_TYPE.match(kind):
                    continue
                if localized_tags(body, param, kind):
                    if external == "_":
                        index = sum(1 for (e, _n, _k, p) in params if e == "_" and p < start)
                        keys.add(("pos", index))
                    else:
                        keys.add(("label", external))
            if keys:
                func_keys.setdefault(name, set()).update(keys)
        for match in STRUCT_DECL.finditer(masked):
            name = match.group(1)
            brace = masked.find("{", match.start())
            body_end = closing_index(masked, brace)
            if body_end == -1:
                continue
            body = body_of[brace:body_end]
            keys = {prop for prop, kind in STRING_PROP.findall(body)
                    if STRING_TYPE.match(kind) and localized_tags(body, prop, kind)}
            if keys:
                struct_keys.setdefault(name, set()).update(keys)

    known = set(func_keys) | set(struct_keys)

    def call_keys(name):
        keys = set(func_keys.get(name, ()))
        keys |= {("label", prop) for prop in struct_keys.get(name, ())}
        return keys

    # Onward passing: a parameter handed to another helper's localized key is localized too.
    for _ in range(8):
        changed = False
        for name, decls in func_decls.items():
            for params, body in decls:
                for external, param, kind, start in params:
                    if not STRING_TYPE.match(kind):
                        continue
                    key = (("pos", sum(1 for (e, _n, _k, p) in params if e == "_" and p < start))
                           if external == "_" else ("label", external))
                    if key in func_keys.get(name, ()):
                        continue
                    for call in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", body):
                        if call.group(1) not in known:
                            continue
                        keys = call_keys(call.group(1))
                        open_index = body.find("(", call.start())
                        close_index = closing_index(body, open_index)
                        if close_index == -1:
                            continue
                        labeled, positional = {}, []
                        for s, e in split_top_level(body, open_index + 1, close_index):
                            argument = body[s:e].strip()
                            label = re.match(r"([A-Za-z_]\w*)\s*:\s", argument)
                            if label:
                                labeled[label.group(1)] = argument
                            else:
                                positional.append(argument)
                        if (("label", external) in keys and param in labeled.values()) or \
                           any(("pos", i) in keys and argument == param for i, argument in enumerate(positional)):
                            func_keys.setdefault(name, set()).add(key)
                            changed = True
                            break
        if not changed:
            break
    return func_keys, struct_keys


def audit_helper_literals(strings):
    """Every literal passed to a helper's localized parameter that has no catalog key."""
    catalog = {normalize_key(key) for key in strings}
    files = sorted(swift_files())
    texts, masks, literals = {}, {}, {}
    for path in files:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        texts[path] = text
        literals[path] = scan_literals(text)
        masks[path] = source_without_literals(text, literals[path])
    func_keys, struct_keys = helper_localized_keys(files, texts, masks)

    def call_keys(name):
        keys = set(func_keys.get(name, ()))
        keys |= {("label", prop) for prop in struct_keys.get(name, ())}
        return keys

    candidates = []
    for path in files:
        text, masked, spans = texts[path], masks[path], literals[path]
        for name in set(func_keys) | set(struct_keys):
            keys = call_keys(name)
            for call in re.finditer(r"\b%s\s*\(" % re.escape(name), masked):
                if re.search(r"\bfunc\s*$", masked[max(0, call.start() - 8):call.start()]):
                    continue
                open_index = masked.find("(", call.start())
                close_index = closing_index(masked, open_index)
                if close_index == -1:
                    continue
                labeled, positional = {}, []
                for s, e in split_top_level(masked, open_index + 1, close_index):
                    label = re.match(r"\s*([A-Za-z_]\w*)\s*:\s", masked[s:e])
                    if label:
                        labeled[label.group(1)] = (s, e)
                    else:
                        positional.append((s, e))
                for key in keys:
                    if key[0] == "pos":
                        if key[1] >= len(positional):
                            continue
                        s, e = positional[key[1]]
                    elif key[1] in labeled:
                        s, e = labeled[key[1]]
                    else:
                        continue
                    for literal in spans:
                        if literal["start"] < s or literal["end"] > e or literal["multiline"]:
                            continue
                        body = literal["body"]
                        if normalize_key(body) in catalog or not is_translatable(body):
                            continue
                        line = text.count("\n", 0, literal["start"]) + 1
                        candidates.append((os.path.relpath(path, REPO), line, name, body))
    seen, unique = set(), []
    for row in candidates:
        if row in seen:
            continue
        seen.add(row)
        unique.append(row)
    return sorted(unique)


def audit_double_localization():
    hits = []
    for path in sorted(swift_files()):
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle.read().splitlines(), 1):
                if DOUBLE.search(line):
                    hits.append((os.path.relpath(path, REPO), number, line.strip()))
    return hits


def audit_chinese_literals():
    hits = []
    for path in sorted(swift_files()):
        if os.path.basename(path) == "LocalizationManager.swift":
            continue  # language names are shown in their own language, by design
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle.read().splitlines(), 1):
                if CJK.search(line):
                    hits.append((os.path.relpath(path, REPO), number, line.strip()))
    return hits


def main():
    catalog = load_catalog()
    strings = catalog.get("strings") or {}
    print("Localization audit")
    print("  catalog         :", os.path.relpath(CATALOG, REPO))
    print("  source language :", catalog.get("sourceLanguage"))
    print()

    translated, missing, no_english = audit_catalog(strings)
    total = len(strings)
    coverage = (len(translated) / total * 100) if total else 0
    print("Catalog")
    print("  keys            :", total)
    print("  with %s   : %d (%.1f%%)" % (TARGET, len(translated), coverage))
    print("  zh-Hans missing :", len(missing))
    for key in sorted(missing)[:40]:
        print("      -", key)
    print("  en missing      :", len(no_english))
    for key in sorted(no_english)[:40]:
        print("      -", key)
    print()

    problems = audit_formats(strings)
    print("Format specifiers (%s vs en)" % TARGET)
    print("  mismatches      :", len(problems))
    for key, source, target in problems[:40]:
        print("      - %r\n          en %r\n          %s %r" % (key, source, TARGET, target))
    print()

    doubles = audit_double_localization()
    print("Double-localization (translated text fed back in as a key)")
    print("  hits            :", len(doubles))
    for path, number, line in doubles[:40]:
        print("      %s:%d  %s" % (path, number, line))
    print()

    chinese = audit_chinese_literals()
    print("Chinese literals in Swift source (should be empty; the catalog holds translations)")
    print("  hits            :", len(chinese))
    for path, number, line in chinese[:40]:
        print("      %s:%d  %s" % (path, number, line))
    print()

    literals = audit_display_literals(strings)
    print("Unlocalized user-visible literals (display-call arguments with no catalog key)")
    print("  remaining       :", len(literals))
    for path, number, call, body in literals:
        print("      %s:%d  %s  %r" % (path, number, call, body))
    print()

    helpers = audit_helper_literals(strings)
    print("Unlocalized helper-passed literals (helpers that localize a String parameter)")
    print("  remaining       :", len(helpers))
    for path, number, name, body in helpers:
        print("      %s:%d  %s(...)  %r" % (path, number, name, body))
    print()

    outstanding = (len(missing) + len(problems) + len(doubles) + len(chinese)
                   + len(literals) + len(helpers))
    print("Outstanding:", outstanding)
    return 1 if outstanding else 0


if __name__ == "__main__":
    sys.exit(main())
