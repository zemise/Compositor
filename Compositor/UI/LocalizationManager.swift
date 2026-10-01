import Foundation
import SwiftUI

/// The languages the app ships: follow the system, English, or Simplified Chinese.
enum AppLanguage: String, CaseIterable, Identifiable {
    case system, english, chinese
    var id: String { rawValue }
    /// `nil` for "follow system", otherwise the app's language identifier.
    var languageCode: String? {
        switch self {
        case .system: return nil
        case .english: return "en"
        case .chinese: return "zh-Hans"
        }
    }
    /// Shown in the menu and settings; language names stay in their own language.
    var title: String {
        switch self {
        case .system: return "Follow System"
        case .english: return "English"
        case .chinese: return "简体中文"
        }
    }
}

/// Runtime language selection.
///
/// SwiftUI content follows the `locale` this publishes through the scene environment, so
/// `Text`/`Button`/`Label` and menu titles re-render the moment the choice changes. Imperative
/// and `String`-typed paths resolve through `localized(_:)`, which reads the matching `lproj`
/// bundle directly — the process locale alone would not change at runtime.
@MainActor @Observable
final class LocalizationManager {
    static let shared = LocalizationManager()

    static let storageKey = "appLanguage.v1"
    static let supportedLanguages = ["en", "zh-Hans"]

    var choice: AppLanguage {
        didSet {
            guard choice != oldValue else { return }
            UserDefaults.standard.set(choice.rawValue, forKey: Self.storageKey)
            applyToAppleLanguages()
        }
    }

    private init() {
        choice = AppLanguage(rawValue: UserDefaults.standard.string(forKey: Self.storageKey) ?? "") ?? .system
    }

    /// True while the process is hosted by the test runner. The suite pins user-facing copy to
    /// English so it can assert on English strings whatever the developer's macOS language is;
    /// a language a test sets explicitly still wins, because only "follow system" is pinned.
    nonisolated static var isRunningTests: Bool {
        NSClassFromString("XCTestCase") != nil
            || ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
    }

    /// The locale injected into the scene environment.
    var locale: Locale {
        if choice == .system && !Self.isRunningTests { return .autoupdatingCurrent }
        return Locale(identifier: resolvedLanguageCode)
    }

    /// The language actually in effect; "follow system" narrows the system preference to a
    /// supported one, and under the test harness resolves to English rather than the system's.
    var resolvedLanguageCode: String {
        if let code = choice.languageCode { return code }
        return Self.isRunningTests ? "en" : Self.systemLanguageCode()
    }

    /// The bundle for the resolved language, falling back to the main bundle.
    var bundle: Bundle { Self.bundle(for: resolvedLanguageCode) ?? .main }

    /// Resolves a `String`-typed, user-visible literal in the selected language, immediately.
    func localized(_ key: String) -> String {
        String(localized: String.LocalizationValue(key), bundle: bundle, locale: locale)
    }

    /// Formats a localized `String`-typed literal with positional arguments.
    func localized(_ key: String, _ arguments: CVarArg...) -> String {
        String(format: localized(key), arguments: arguments)
    }

    /// Same lookup for code that is not main-actor isolated (importers, renderers).
    nonisolated static func localizedString(_ key: String) -> String {
        let choice = AppLanguage(rawValue: UserDefaults.standard.string(forKey: storageKey) ?? "") ?? .system
        let code = choice.languageCode ?? (isRunningTests ? "en" : systemLanguageCode())
        let bundle = bundle(for: code) ?? .main
        return String(localized: String.LocalizationValue(key), bundle: bundle, locale: Locale(identifier: code))
    }

    /// Formats a localized `String`-typed literal with positional arguments, off the main actor.
    nonisolated static func localizedFormat(_ key: String, _ arguments: CVarArg...) -> String {
        String(format: localizedString(key), arguments: arguments)
    }

    static func resolvedLocale(for choice: AppLanguage) -> Locale {
        guard let code = choice.languageCode else { return .autoupdatingCurrent }
        return Locale(identifier: code)
    }

    /// The first supported language among the user's system preferences.
    nonisolated static func systemLanguageCode() -> String {
        for preferred in Locale.preferredLanguages {
            let code = Locale(identifier: preferred).language.languageCode?.identifier ?? preferred
            if let match = supportedLanguages.first(where: { Locale(identifier: $0).language.languageCode?.identifier == code }) {
                return match
            }
        }
        return "en"
    }

    nonisolated static func bundle(for code: String) -> Bundle? {
        guard let path = Bundle.main.path(forResource: code, ofType: "lproj") else { return nil }
        return Bundle(path: path)
    }

    /// Mirrors the choice into `AppleLanguages` so AppKit-owned surfaces and the next launch agree.
    private func applyToAppleLanguages() {
        let defaults = UserDefaults.standard
        if let code = choice.languageCode {
            defaults.set([code], forKey: "AppleLanguages")
        } else {
            defaults.removeObject(forKey: "AppleLanguages")
        }
    }
}

/// Re-applies the selected locale to a floating panel's SwiftUI content and refreshes it live.
struct LocalizedRoot<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        content.environment(\.locale, LocalizationManager.shared.locale)
    }
}
