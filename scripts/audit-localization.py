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

    outstanding = len(missing) + len(problems) + len(doubles) + len(chinese) + len(literals)
    print("Outstanding:", outstanding)
    return 1 if outstanding else 0


if __name__ == "__main__":
    sys.exit(main())
