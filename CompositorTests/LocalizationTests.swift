import Foundation
import Testing
@testable import Compositor

/// Simplified Chinese localization: catalog completeness, runtime resolution, and the language choice.
@MainActor
@Suite(.serialized)
struct LocalizationTests {
    /// The catalog in the source checkout; the tests run from the repository root's parents.
    private var catalogURL: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Compositor/Localizable.xcstrings")
    }

    private func catalogStrings() throws -> [String: Any] {
        let data = try Data(contentsOf: catalogURL)
        let root = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(root["sourceLanguage"] as? String == "en")
        #expect(root["version"] as? String == "1.0")
        return try #require(root["strings"] as? [String: Any])
    }

    /// One key's value for a language, read from the source catalog.
    private func catalogValue(_ key: String, _ language: String, in strings: [String: Any]) -> String? {
        let localizations = (strings[key] as? [String: Any])?["localizations"] as? [String: Any]
        let unit = (localizations?[language] as? [String: Any])?["stringUnit"] as? [String: Any]
        return unit?["value"] as? String
    }

    /// Resolves a key against the shipped bundle for a language.
    private func resolve(_ key: String, language: String) -> String {
        let bundle = LocalizationManager.bundle(for: language) ?? .main
        return String(localized: String.LocalizationValue(key), bundle: bundle, locale: Locale(identifier: language))
    }

    /// True when the string contains a CJK ideograph (U+3400–U+9FFF).
    private func containsCJK(_ text: String) -> Bool {
        text.unicodeScalars.contains { (0x3400...0x9FFF).contains($0.value) }
    }

    // MARK: - The test-harness language default

    /// The language state a test borrows from the environment and must put back.
    private struct LanguageSnapshot {
        let storedChoice: String?
        let appleLanguages: [String]?
        let choice: AppLanguage
    }

    private func snapshotLanguage() -> LanguageSnapshot {
        let defaults = UserDefaults.standard
        return LanguageSnapshot(storedChoice: defaults.string(forKey: LocalizationManager.storageKey),
                                appleLanguages: defaults.stringArray(forKey: "AppleLanguages"),
                                choice: LocalizationManager.shared.choice)
    }

    /// Puts back what `snapshotLanguage()` captured, so a test cannot leak a language into another suite.
    private func restore(_ snapshot: LanguageSnapshot) {
        let defaults = UserDefaults.standard
        LocalizationManager.shared.choice = snapshot.choice
        if let value = snapshot.storedChoice { defaults.set(value, forKey: LocalizationManager.storageKey) }
        else { defaults.removeObject(forKey: LocalizationManager.storageKey) }
        if let value = snapshot.appleLanguages { defaults.set(value, forKey: "AppleLanguages") }
        else { defaults.removeObject(forKey: "AppleLanguages") }
    }

    /// Under the test harness "follow system" resolves to English, so the suite can assert English
    /// copy on any developer's machine. An explicit choice still wins — see
    /// `languageChoicePersistsAndResolves`, which selects zh-Hans and gets Chinese back.
    @Test func testHarnessDefaultsToEnglish() {
        #expect(LocalizationManager.isRunningTests, "the English-under-test seam is not active")
        let snapshot = snapshotLanguage()
        defer { restore(snapshot) }
        LocalizationManager.shared.choice = .system
        #expect(LocalizationManager.shared.resolvedLanguageCode == "en")
        #expect(LocalizationManager.shared.locale.identifier.hasPrefix("en"))
        // The same seam reaches the String/AppKit path, not only the SwiftUI environment.
        #expect(LocalizationManager.localizedString("Layers") == "Layers")
    }

    /// The "no untranslated leftovers" guard: every declared key carries a non-empty value in
    /// both languages. A key with no English value is as much a gap as one with no Chinese.
    @Test func everyCatalogKeyHasChinese() throws {
        let strings = try catalogStrings()
        #expect(!strings.isEmpty)
        var missingChinese: [String] = []
        var missingEnglish: [String] = []
        for key in strings.keys {
            if (catalogValue(key, "zh-Hans", in: strings) ?? "").isEmpty { missingChinese.append(key) }
            if (catalogValue(key, "en", in: strings) ?? "").isEmpty { missingEnglish.append(key) }
        }
        #expect(missingChinese.isEmpty, "keys without a Simplified Chinese value: \(missingChinese.sorted().prefix(20))")
        #expect(missingEnglish.isEmpty, "keys without an English value: \(missingEnglish.sorted().prefix(20))")
    }

    /// Simplified Chinese ships in the built app, not only in the source catalog.
    @Test func chineseBundleShipsInTheApp() throws {
        #expect(LocalizationManager.bundle(for: "zh-Hans") != nil, "zh-Hans.lproj missing from the app bundle")
        #expect(resolve("Layers", language: "zh-Hans") == "图层")
        #expect(resolve("Opacity", language: "zh-Hans") == "不透明度")
    }

    @Test func representativeKeysDifferBetweenLanguages() throws {
        for key in ["Layers", "New Canvas…", "Export PNG…", "Keyboard Shortcuts…", "Language"] {
            let chinese = resolve(key, language: "zh-Hans")
            let english = resolve(key, language: "en")
            #expect(chinese != english, "\(key) resolved the same in both languages")
            #expect(!chinese.isEmpty)
        }
        // A menu ellipsis survives translation.
        #expect(resolve("New Canvas…", language: "zh-Hans").hasSuffix("…"))
    }

    /// Prose, not just single-word labels: a status-bar hint, a tooltip, a dialog message, and a
    /// menu item all resolve to Chinese in-process through the shipped zh-Hans bundle.
    @Test func chineseProseResolvesThroughTheShippedBundle() throws {
        let prose = [
            "Drag to pan · Pinch to zoom",                        // status-bar hint
            "Open in a new project tab",                          // .help tooltip
            "Your changes will be lost if you don’t save them.",  // dialog message
            "New Canvas…",                                        // menu item
        ]
        for key in prose {
            let chinese = resolve(key, language: "zh-Hans")
            let english = resolve(key, language: "en")
            #expect(!chinese.isEmpty)
            #expect(chinese != english, "\(key) resolved the same in both languages")
            #expect(containsCJK(chinese), "\(key) has no Chinese characters: \(chinese)")
        }
    }

    /// Every key's zh-Hans `%`-format specifiers match its English ones, so a translation that
    /// swaps `%@` for `%lld` — a runtime crash — is caught here rather than in the app.
    @Test func chineseFormatSpecifiersMatchEnglish() throws {
        let strings = try catalogStrings()
        var mismatches: [String] = []
        for key in strings.keys {
            guard let english = catalogValue(key, "en", in: strings),
                  let chinese = catalogValue(key, "zh-Hans", in: strings) else { continue }
            if try formatSpecifiers(english) != formatSpecifiers(chinese) { mismatches.append(key) }
        }
        #expect(mismatches.isEmpty, "keys whose zh-Hans format specifiers differ from en: \(mismatches.sorted().prefix(20))")
    }

    /// The `%`-format specifiers in a string, normalized so `%@` and `%1$@` compare equal.
    private func formatSpecifiers(_ text: String) throws -> [String] {
        let pattern = #"%(?:\d+\$)?[-+#0]*\d*(?:\.\d+)?(?:hh|h|ll|l|z|j|t|q|L)?[A-Za-z@]"#
        let regex = try NSRegularExpression(pattern: pattern)
        let range = NSRange(text.startIndex..., in: text)
        return regex.matches(in: text, range: range).compactMap { match in
            guard let span = Range(match.range, in: text) else { return nil }
            let normalized = String(text[span]).replacingOccurrences(of: "$", with: "")
            return String(normalized.drop { "%0123456789".contains($0) })
        }.sorted()
    }

    /// A key that ends in the menu ellipsis keeps it in zh-Hans, so menu items still read as
    /// commands that open something.
    @Test func chineseKeepsTheMenuEllipsis() throws {
        let strings = try catalogStrings()
        var dropped: [String] = []
        for key in strings.keys where key.hasSuffix("…") {
            guard let chinese = catalogValue(key, "zh-Hans", in: strings) else { continue }
            if !chinese.hasSuffix("…") { dropped.append(key) }
        }
        #expect(dropped.isEmpty, "keys that lost the … ellipsis in zh-Hans: \(dropped.sorted().prefix(20))")
    }

    /// The choice persists, resolves to the right locale, and drives the language code. Asserted
    /// on what the manager *writes* and *resolves*, then restored — never on a pristine global
    /// default, which the machine's own `AppleLanguages` would otherwise break.
    @Test func languageChoicePersistsAndResolves() throws {
        let snapshot = snapshotLanguage()
        defer { restore(snapshot) }

        LocalizationManager.shared.choice = .chinese
        #expect(UserDefaults.standard.string(forKey: LocalizationManager.storageKey) == "chinese")
        #expect(LocalizationManager.shared.resolvedLanguageCode == "zh-Hans")
        #expect(LocalizationManager.shared.locale.identifier.hasPrefix("zh"))
        // An explicit choice wins over the English-under-test default.
        #expect(LocalizationManager.shared.localized("Layers") == "图层")
        // The choice is mirrored into AppleLanguages so AppKit-owned surfaces agree with it.
        #expect(UserDefaults.standard.stringArray(forKey: "AppleLanguages") == ["zh-Hans"])

        LocalizationManager.shared.choice = .english
        #expect(LocalizationManager.shared.resolvedLanguageCode == "en")
        #expect(LocalizationManager.shared.locale.identifier.hasPrefix("en"))
        #expect(UserDefaults.standard.stringArray(forKey: "AppleLanguages") == ["en"])

        // Follow System has no fixed code, and resolves to a supported language.
        LocalizationManager.shared.choice = .system
        #expect(UserDefaults.standard.string(forKey: LocalizationManager.storageKey) == "system")
        #expect(AppLanguage.system.languageCode == nil)
        #expect(LocalizationManager.resolvedLocale(for: .system) == Locale.autoupdatingCurrent)
        #expect(LocalizationManager.supportedLanguages.contains(LocalizationManager.systemLanguageCode()))
    }

    /// A curated guard: UI strings the compiler extracts must be present in the catalog.
    @Test func knownUIStringsAreInTheCatalog() throws {
        let strings = try catalogStrings()
        let expected = [
            "Layers", "Opacity", "Mask", "Multiply",
            "New Canvas…", "Open Project…", "Save As…", "Export PNG…", "Export JPEG…",
            "Keyboard Shortcuts…", "Grid Settings…", "Check for Updates…",
            "Undo", "Copy", "Paste", "Deselect", "Invert",
            "Language", "Follow System",
            // The keyboard-shortcut table is built from String literals, not extracted keys.
            "Brush tool", "Canvas & Layers", "Menus", "Text Editing",
            "Opacity digit 3 (type two for exact %)", "Nudge Left 1 px",
        ]
        let missing = expected.filter { strings[$0] == nil }
        #expect(missing.isEmpty, "UI strings missing from the catalog: \(missing)")
    }
}
