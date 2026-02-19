# Not Working / To Pick Up

*Last updated: 2025-02-20 — good commit point; inputs/slots visible, preview area shows.*

## Still broken / incomplete

- **Capture/automation not actually working** — UI shows inputs and slots, but end-to-end capture/automation behavior is not confirmed working.
- **UI does not match main (mockup)** — Layout, spacing, and visual hierarchy need to be brought in line with the main reference (e.g. mockup/cooldown-reader-mockup-v2.html or settings-redesign-mockup-v2.html). Polish and alignment with design spec still needed.

## Working as of this commit

- Config loads on startup (no overwrite).
- Enable/Disable reflects automation state.
- Slot state buttons appear and are populated from config.
- Live Preview area receives frames and shows the captured image (same widget instance is updated).
- Settings dialog opens; import/export; overlay and capture region config.
