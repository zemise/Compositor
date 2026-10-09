# Community release validation

## Apple silicon launch failure in 1.4.10

The macOS 15.3 arm64 crash reports from October 9, 2026 show a launch-time `DYLD`
termination while loading `Sparkle.framework`: the process and library have different
Team IDs. Both binaries are Universal 2 and ad-hoc signed, with no Team ID. The main
app also has Hardened Runtime enabled, which enforces library validation.

`codesign --verify --deep --strict` passes for the affected installer: it verifies the
signatures on disk, but does not establish that dyld will allow the app to load a library.
Removing the main app's runtime flag on a copy of the same installer lets it launch
natively on Apple silicon. This does not require Rosetta or changes to the renderer.

Sparkle documents this restriction in its [installation guide](https://sparkle-project.org/documentation/).

The GitHub community workflow now builds with `ENABLE_HARDENED_RUNTIME=NO`, manual
ad-hoc signing and no development team. Debug builds also disable Hardened Runtime
for their local ad-hoc signature. The Developer ID Release configuration and
`scripts/release.sh` keep Hardened Runtime enabled for signed and notarized releases.

## Release gate

The Intel runner builds both slices and validates the signature. It attempts
`scripts/smoke-test-app.sh` to check that the app remains running for ten seconds.
On the hosted Intel runner, the specific Metal assertion `Target device architecture is nil`
is reported as an unavailable GUI check, after signature and architecture validation. Other
startup errors remain fatal. Native Intel GUI behavior still requires a real Mac.
An Apple silicon runner downloads the packaged installer, validates the checksums,
unpacks the ZIP and runs the same native startup check before publishing the release.
Signature errors are fatal rather than warnings.

To reproduce locally on Apple silicon:

```sh
xcodebuild build -project Compositor.xcodeproj -scheme Compositor \
  -configuration Release -destination 'generic/platform=macOS' \
  -derivedDataPath build/DerivedData ARCHS='arm64 x86_64' ONLY_ACTIVE_ARCH=NO \
  CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM='' \
  ENABLE_HARDENED_RUNTIME=NO
scripts/smoke-test-app.sh build/DerivedData/Build/Products/Release/Compositor.app arm64
```

The startup check detects early loader failures; it does not test editing interactions
or replace Gatekeeper assessment of a notarized release.

## Upstream integration

Integrated upstream `robbietilton/Compositor` main at
`3e099482765b084726ae150a6de2586ba5766d60` (52 commits since the shared base
`710dd66850496dbb1ac012fe9caa444e8cb738eb`). The update adds command search, canvas-only
fullscreen, 90-degree canvas rotation, Export As with PNG/JPEG/PDF previews, Last Filter,
the dedicated Scanlines filter, and the Navigator minimap. It also improves Camera Raw, RAW import, layer
reveal behavior, scrolling tool headers and slider tests.

Merge resolutions retain the fork's macOS 15/Intel support, Simplified Chinese
localization and language switching. New interface strings are translated, including
command palette tool entries and export dialogs. The fork version advances to 1.4.11
(build 46) without changing the project file format.

## Local verification on October 9, 2026

- Xcode 16.4 on an Apple silicon Mac running macOS 15.3.
- Universal 2 Release build for `arm64` and `x86_64`.
- Full `CompositorTests`: 552 tests passed after the first upstream merge.
- After the subsequent Navigator merge: all 23 Navigator, command palette and
  localization tests passed, including the nine new Navigator tests.
- Localization audit: no missing translations, format mismatches or uncovered labels.
- Native arm64 startup check on the Release app and the unpacked ZIP.

The updated Intel slice is compiled locally; the hosted Intel runner aborts in its Metal driver, so native Intel GUI startup still
needs verification on a real Intel Mac.

A failed tag release can be retried with the Release workflow’s manual dispatch, passing the
existing version tag. Both build and publish jobs check out that tag; the tag is never moved.
