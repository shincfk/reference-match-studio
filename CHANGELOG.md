# Changelog

All notable changes to Reference Match Studio are documented here.

## v0.1.1 — 2026-08-25

- Fixed Viewer Fit behavior for portrait and high-resolution stills; the complete image is now contained inside the monitoring area by default.
- Added continuous mouse-wheel and trackpad zoom with pointer-position anchoring.
- Added zoom buttons, original-pixel view, Fit reset, drag panning, double-click reset and keyboard shortcuts.

## v0.1.0 — 2026-08-24

First public release.

- Added the configuration-driven `ReferenceMatch.dctl` engine and `Reference Match Bridge`.
- Added strict `.rmatch.json` Profile schema, validation and activation workflow.
- Added a local professional grading workspace with source/reference/result/split/difference viewers, scopes and signal delta readouts.
- Added Profile import, save/download and Resolve installation guidance.
- Added unit tests for Profile validation and DCTL header generation.

### Known limitations

- `shotMatch` is a global statistical transfer calibrated for similar source material; it is not a semantic or scene-independent grade.
- Resolve DCTL cannot parse JSON at render time. Activate a Profile to generate `ReferenceMatchProfile.h`, then place it beside `ReferenceMatch.dctl` in Resolve's LUT/DCTL directory.
- The tool is local-only and does not package a Resolve plug-in or a signed desktop application in this release.
