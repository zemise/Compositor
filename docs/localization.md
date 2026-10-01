# Localization

Compositor ships in English (`en`, the source language) and Simplified Chinese (`zh-Hans`). The
in-app language can be changed at runtime, without relaunching.

## Where strings live

All user-facing strings live in one Xcode String Catalog:

- `Compositor/Localizable.xcstrings`

It is the single source of truth. Every key has an explicit `en` and `zh-Hans` value.

Two kinds of strings end up there:

- **Automatic.** SwiftUI treats string literals in `Text(…)`, `Button(…)`, `Label(…)`,
  `Toggle(…)`, `Picker(…)`, `TextField(…)`, `Menu(…)`, `Section(…)`, `.help(…)`, `.navigationTitle(…)`,
  `.alert(…)` and the app's `Commands` as localizable keys. The compiler extracts them; you don't
  write any lookup yourself. Interpolated literals become format keys (`"Undo %@"`).
- **Explicit.** Text that reaches the UI as a plain `String` is not extracted. These are resolved
  through `LocalizationManager.localized(_:)` (main actor) or
  `LocalizationManager.localizedString(_:)` / `localizedFormat(_:_:)` (any thread). That covers
  `FloatingPanel` and `NSWindow` titles, `NSMenuItem` titles, the keyboard-shortcut table,
  import/export messages, history entry names, and enum `rawValue`s that are shown as labels
  (wrapped in `LocalizedStringKey(rawValue)` at the display site).

Enums that are `Codable` or otherwise persisted (for example `AdjustmentKind`) keep their English
`rawValue` on disk — only the displayed label is localized, so the language never leaks into a
saved `.comp` project.

## Adding a string

1. Use a literal in an auto-localizable position if you can. If the compiler can see it, it will be
   extracted into the catalog on the next build.
2. If the string must be a `String` (a window title, an AppKit control), pass it through
   `LocalizationManager.localized(_:)` (or the `nonisolated` variants for background code).
3. Fill in the `zh-Hans` value in `Compositor/Localizable.xcstrings`. **No key may ship without one**
   — `LocalizationTests.everyCatalogKeyHasChinese` fails the build if it does.

## Adding a language

1. Add the language identifier to `CFBundleLocalizations` in `Config/Info.plist` and to
   `knownRegions` in `Compositor.xcodeproj/project.pbxproj`.
2. Add it to `LocalizationManager.supportedLanguages` and add a case to `AppLanguage`.
3. Add the new language to `Compositor/Localizable.xcstrings` (Xcode: **+** under the language list,
   or add a `localizations` entry per key).
4. Translate every key. `LocalizationTests` will tell you what is missing.

## How runtime switching works

`LocalizationManager` (in `Compositor/UI/LocalizationManager.swift`) holds the choice — **Follow
System**, **English**, or **简体中文** — and persists it to `UserDefaults` under `appLanguage.v1`.

- **SwiftUI content** follows the scene environment. The app injects
  `.environment(\.locale, LocalizationManager.shared.locale)` on the window's content and on the
  scene, so `Text`, `Button`, `Label` and every menu title re-render the instant the choice changes.
  Floating panel content is wrapped in `LocalizedRoot`, which re-applies the same locale.
- **`String`-typed and AppKit surfaces** resolve through `LocalizationManager`, which reads the
  matching `zh-Hans.lproj` / `en.lproj` bundle directly rather than the process locale (the process
  locale cannot change at runtime). Panel and menu titles are re-read when the panel is next shown.
- The choice is also written to `AppleLanguages` in `UserDefaults`, so AppKit-owned surfaces and the
  next launch agree with it.

The switcher appears in two places: **Compositor ▸ Language** in the app menu (with the current
choice checked) and a **Language** row in the Keyboard Shortcuts window (⌘/).

### Limitations

- A panel that is already open updates its SwiftUI content live; AppKit panel *titles* and menu
  items pick up the new language the next time they are shown or opened.
- Persisted default layer names (`Layer 1`, `Folder 2`) and the `.comp` format stay English, so the
  language never changes what is written to disk. Relaunch to fully re-localize a project that is
  already open in a tab.

## Maintaining this fork

This is a fork of Compositor, so the localization is kept deliberately small and separate so that
an upstream update does not lose it.

### The seam

Four upstream files carry infrastructure, each a small, self-contained change:

| File | Change | Re-apply if upstream rewrites it |
| --- | --- | --- |
| `Compositor.xcodeproj/project.pbxproj` | `zh-Hans` in `knownRegions` | add the region back |
| `Config/Info.plist` | `CFBundleDevelopmentRegion` and `CFBundleLocalizations` | re-add the two keys |
| `Compositor/CompositorApp.swift` | `.environment(\.locale, …)` on the window and the scene, plus the Language menu | re-inject the locale and the menu |
| `Compositor/UI/FloatingPanel.swift` | hosts panel content in `LocalizedRoot` | wrap the content again |

Every *other* modified upstream file is a call-site conversion: a `String` value that reaches the
UI (an enum `rawValue`, a window or menu title, an edit or selection name) has to be wrapped so
SwiftUI, or `LocalizationManager`, can translate it. Those are one-line changes, and a conflict in
one of them loses only that one string's translation. No `.comp` format, `ProjectManifest.current`,
or workflow file is touched.

### What survives an upstream update, and why

The translations live in `Compositor/Localizable.xcstrings`, and every entry is keyed by the
**English source string**. Upstream never edits that file, so:

- an upstream change to a *translated* string arrives as a changed key — the old catalog entry
  stops matching and the string shows in English until it is re-added; nothing crashes;
- an upstream change to an *untranslated* string needs no work — the catalog drives the build;
- a **new** upstream UI string appears as an untranslated key. Run
  `python3 scripts/audit-localization.py` to list missing keys and candidate `String` display
  sites, then add the `zh-Hans` value.

### Rebasing on upstream

```sh
git fetch upstream
git rebase upstream/main
```

Resolve conflicts in the four seam files by re-applying the change above, and in a call-site file
by keeping the `LocalizedStringKey(…)` / `LocalizationManager.localized(…)` wrap. Then rebuild and
run the tests:

```sh
xcodebuild build-for-testing -project Compositor.xcodeproj -scheme Compositor \
  -destination 'platform=macOS,arch=arm64' -configuration Debug -derivedDataPath DerivedData CODE_SIGN_IDENTITY=-
```

`LocalizationTests.everyCatalogKeyHasChinese` fails the suite if any key loses its `zh-Hans`
value, so a rebase that drops translations is caught rather than shipped.

## What is deliberately not translated

Excluded on purpose, not missed. Adding these to the catalog would either break something or put
the language somewhere it does not belong.

- **SF Symbol names** — `NavigationTool.symbol`, and every `Image(systemName:)` argument. They are
  identifiers, not text; translating one breaks the icon.
- **Keyboard glyphs and unit letters** — `⌘ ⌥ ⇧ ⌃ ⌫ ⏎`, the `…` menu suffix, `R`, `G`, `B` (channel
  letters, which Photoshop keeps as-is), `X`, `Y` (axis letters) and `°`. They read the same in
  every language.
- **Persisted enum `rawValue`s** — blend modes, adjustment kinds, brush modes and effect kinds
  write their English `rawValue` into `.comp` files. Only their display labels are localized, so the
  language never reaches a saved project. For the same reason the default names a new document hands
  out (`Layer 1`, `Folder 1`) stay English: they are document data, and localizing them would leave
  a project with names in whichever language happened to be active when each layer was created.
- **Model-level identifiers** — `DocumentHistory.undoName` / `redoName` stay English. The Undo and
  Redo menu items localize both the template and the name when they display it (`"Undo %@"` plus the
  looked-up name), so the interface is Chinese while the history records something stable whose
  meaning does not change with the language.
- **Shader source** — the Metal strings under `Rendering/` are compiled code.

### Why the Info.plist names are still English

`CFBundleTypeName` ("Images", "Compositor Project") and `UTTypeDescription` ("Compositor Layer",
"Adobe Photoshop Document", …) are visible to the user — Finder's Kind column, and the file-type
popup in the open and save panels. They are **not** localized here, and that is a limitation of the
mechanism rather than an oversight: `InfoPlist.strings` and `InfoPlist.xcstrings` only localize
**root-level** Info.plist keys, and these live inside the array-of-dictionaries under
`CFBundleDocumentTypes`, `UTExportedTypeDeclarations` and `UTImportedTypeDeclarations`. Reaching them
would mean restructuring how the app declares its document types, and localizing several entries
that share one key (`CFBundleTypeName` appears twice) is ambiguous by construction.

So no `InfoPlist.strings` is shipped. An empty or partial one would look like it worked and quietly
do nothing, which is worse than leaving the names in English. The only root-level user-visible keys
this app has are `CFBundleName` (the product name, which should not be translated) and
`NSHumanReadableCopyright` (empty).
