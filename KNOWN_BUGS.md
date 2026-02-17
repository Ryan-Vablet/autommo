# Known Bugs

This file tracks known issues that are deferred or low priority. Functionality (in-game key sending, capture, detection) is prioritized; these are documented for future fixes or reference.

---

## 1. Scroll / left panel flicker

**What happens:** The left column (scroll area containing Last Action and Next Intention) sometimes flickers or jitters. The effect is inconsistent and often goes away after:

- Resizing the window
- Clicking between the app and other windows (focus changes)
- Waiting until all three Last Action rows are populated

**Likely cause:** Qt layout or redraw behavior—something in the scroll area or the stacked/panel layout triggers repeated layout passes or repaints. Not tied to a single widget; fixing one thing (e.g. fixed heights) has made it worse in the past.

**What was tried:** Using fixed heights on the Last Action panel and history widget to stabilize layout. Result: flicker got worse and the panel no longer resized with content, so those changes were reverted.

**Status:** Deferred. Reproduce when possible, then investigate with minimal layout/visibility changes (e.g. `updateGeometry`, `setUpdatesEnabled`, or style/background tweaks) rather than more fixed sizing.

---

## 2. Last Action: duplicate entries and wrong timings

**What happens:**

- The same skill is logged multiple times in Last Action with short gaps (e.g. 0.2 s, 1.0 s) when it should only fire once per GCD.
- Example: Judgment (10 s cooldown) appears as “Judgment 1.0 s” and “Judgment 1.4 s” in a row.
- Example: Deconstruct appears several times in a row with 0.2 s or 1.0 s between entries.
- Real GCD is ~1.4 s; the log often shows 1.0 s (from the send-block timeout) or 0.2 s (from the same key being sent again before the next frame).

**Likely cause:** After we send a key, the next capture frame often still shows that slot as READY (game/client hasn’t updated the bar yet). The automation then sends the same key again (and sometimes again) before the next frame shows the slot on cooldown. So we get multiple “Sent key: N” for the same slot and multiple Last Action entries with sub-GCD timings.

**Branch / approach that was reverted:** A branch implemented “block send until slot not ready”:

- After sending, we didn’t send again until a captured frame showed that slot as not READY (on_cooldown / gcd).
- A timeout (configurable, default 1.0 s) cleared the block so the app didn’t get stuck if the game never updated in our capture.
- On timeout, we excluded that slot for one send so we didn’t fire the same key again and instead sent the next ready skill.

That branch is kept in the repo for reference but was rolled back on main. In practice, in-game button pushing felt better with the old logic (no blocking), so the current behavior is: no send blocking; duplicate/mis-timed Last Action entries are accepted for now.

**Status:** Deferred. When revisiting:

- Consider a lighter approach (e.g. only skip one frame after send, or a very short minimum interval) so we don’t change “when” we send as much as the previous branch did.
- Or fix only the *logging* side (e.g. coalesce or filter duplicate slot entries / timings) so the UI reflects reality better without changing send logic.

---

## 3. Other notes

- **Next Intention counter:** The live “time since last action” counter and the rule “only reset on actual send, not when intention appears” are in place and correct. No known bug there.
- **Default window size / send block timeout:** Default open size and the (now-removed) send block timeout setting are not bugs; they were adjusted and then removed with the branch rollback.

---

## Summary

| Bug | Severity | Status |
|-----|----------|--------|
| Scroll / left panel flicker | Annoying, cosmetic | Deferred |
| Last Action duplicates and wrong timings | Misleading log, no wrong keypresses | Deferred |

Functionality (capture, detection, key sending, priority order) is correct; these items are about display stability and log accuracy.
