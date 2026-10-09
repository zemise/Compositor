import AppKit
import Testing
@testable import Compositor

@MainActor
struct CommandPaletteTests {
    private func entry(_ id: String, enabled: Bool = true) -> CommandPaletteEntry {
        CommandPaletteEntry(id: id, shortcut: nil, isEnabled: enabled, perform: {})
    }

    @Test func lettersInOrderMatchAndWordStartsCount() {
        #expect(CommandPaletteSearch.score("gb", in: "Filter › Gaussian Blur…") != nil)
        #expect(CommandPaletteSearch.score("BLUR", in: "Filter › Gaussian Blur…") != nil, "case doesn't matter")
        #expect(CommandPaletteSearch.score("bg", in: "Filter › Gaussian Blur…") == nil, "letters must come in order")
        #expect(CommandPaletteSearch.score("", in: "Anything") == 0)
        let wordStarts = CommandPaletteSearch.score("gb", in: "Filter › Gaussian Blur…")!
        let midWord = CommandPaletteSearch.score("gb", in: "Edit › Debug Tab")!
        #expect(wordStarts > midWord)
    }

    @Test func rankingPutsTheBestAndEnabledFirst() {
        let entries = [entry("Edit › Paste"), entry("Layer › Flip Layer Horizontal"),
                       entry("Filter › Gaussian Blur…"), entry("Filter › Motion Blur…"),
                       entry("Edit › Undo", enabled: false), entry("Image › Levels…")]
        #expect(CommandPaletteSearch.rank(entries, query: "gau").first?.id == "Filter › Gaussian Blur…")
        #expect(CommandPaletteSearch.rank(entries, query: "blur").map(\.id) == ["Filter › Gaussian Blur…", "Filter › Motion Blur…"]
                || CommandPaletteSearch.rank(entries, query: "blur").map(\.id) == ["Filter › Motion Blur…", "Filter › Gaussian Blur…"])
        #expect(CommandPaletteSearch.rank(entries, query: "fl").first?.id == "Layer › Flip Layer Horizontal")
        // No query: everything, enabled first, in menu order.
        let all = CommandPaletteSearch.rank(entries, query: "")
        #expect(all.count == entries.count && all.last?.id == "Edit › Undo" && all.first?.id == "Edit › Paste")
        #expect(CommandPaletteSearch.rank(entries, query: "e u").map(\.id).last == "Edit › Undo", "disabled after enabled")
    }

    /// Typing a tool's name finds the tool first, not a long title that happens to hold its letters scattered.
    @Test func wholeWordsBeatScatteredLetters() {
        let entries = [entry("Layer › New Adjustment Layer › Hue/Saturation…"), entry("Tool › Lasso"), entry("Tool › Polygonal Lasso")]
        #expect(CommandPaletteSearch.rank(entries, query: "lasso").map(\.id) == ["Tool › Lasso", "Tool › Polygonal Lasso",
                                                                               "Layer › New Adjustment Layer › Hue/Saturation…"])
        let model = CommandPaletteModel(entries: entries)
        model.query = "lasso"
        model.move(by: 1)
        model.query = "lasso"
        #expect(model.selected?.id == "Tool › Polygonal Lasso", "Return writing the same text back keeps the choice")
    }

    /// Each typed word starting a word ranks a title high, the shortest first: "new layer" is New Blank Layer.
    @Test func typedWordsStartingWords() {
        let entries = [entry("Layer › New Adjustment Layer › Curves…"), entry("Layer › New Adjustment Layer › Exposure…"),
                       entry("Layer › Duplicate Layer"), entry("Layer › New Blank Layer")]
        #expect(CommandPaletteSearch.rank(entries, query: "new layer").first?.id == "Layer › New Blank Layer")
    }

    /// Against the app's own SwiftUI menu bar, not a hand-built one: its commands are listed, disabled ones greyed
    /// (SwiftUI takes their action away), and running one runs its SwiftUI action.
    @Test func realMenuBarRunsItsCommands() async throws {
        let bar = try #require(NSApp.mainMenu)
        func entries() -> [CommandPaletteEntry] { CommandPaletteMenu.entries(in: bar, skipping: CommandPaletteController.skipped) }
        // On macOS 15, SwiftUI's native menu titles can follow the process language even when the
        // scene's locale is English under the test harness. Match shipped labels in either language.
        func labels(_ key: String) -> Set<String> {
            Set(["en", "zh-Hans"].map { language in
                let bundle = LocalizationManager.bundle(for: language) ?? .main
                return String(localized: String.LocalizationValue(key), bundle: bundle, locale: Locale(identifier: language))
            })
        }
        func hasLabel(_ entry: CommandPaletteEntry, _ key: String) -> Bool {
            entry.title.components(separatedBy: " › ").last.map { labels(key).contains($0) } ?? false
        }
        func gridState() -> NSControl.StateValue? {
            bar.items.compactMap(\.submenu).flatMap(\.items).first {
                labels("Pixel Grid (800% and above)").contains($0.title)
            }?.state
        }
        let listed = entries()
        #expect(listed.contains { hasLabel($0, "Gaussian Blur…") })
        #expect(!listed.contains { hasLabel($0, "Search Commands…") })
        #expect(!listed.contains { $0.title.components(separatedBy: " › ").first.map { labels("Window").contains($0) || labels("Help").contains($0) } ?? false })
        // The test host has no document open, so Zoom In is disabled: listed, greyed.
        let zoom = try #require(listed.first { hasLabel($0, "Zoom In") })
        #expect(!zoom.isEnabled)
        let grid = try #require(listed.first { hasLabel($0, "Pixel Grid (800% and above)") })
        let before = try #require(gridState())
        grid.perform()
        try await Task.sleep(for: .milliseconds(300))
        let after = entries() // Reading the menu again refreshes it, as opening the palette does.
        #expect(gridState() != before, "the toggle's SwiftUI binding flipped")
        #expect(after.first { $0.title == grid.title }?.isOn != grid.isOn, "and the palette's checkmark follows it")
        grid.perform() // Put it back.
        try await Task.sleep(for: .milliseconds(300))
    }
}
