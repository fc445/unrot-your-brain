# Spike: Sparkle auto-updates while builds are signed ad hoc

**Ticket:** [PR-35](https://linear.app/freddie-cassidy/issue/PR-35/spike-can-sparkle-auto-update-the-mac-app-while-builds-are-signed-ad) · **Date:** 2026-09-26 · **Sparkle:** 2.10.0 · **macOS:** 27.2 (arm64), Xcode 27.0

## Question

The Mac app has no update mechanism. Sparkle 2 is the standard self-updater, but
release DMGs are signed ad hoc (`packaging/dmg.sh`: no Developer ID, no hardened
runtime). Every build therefore has a different code signature. Sparkle checks
code signatures as well as its own EdDSA signature. Does it install an update at
all in that setup? And what else breaks around it?

1. Does an ad-hoc → ad-hoc update install, given a valid EdDSA signature?
2. Can one appcast serve both channels: dev items hidden from prod builds and offered to dev builds?
3. After an update, can the new build read the Keychain item the old build wrote, without a prompt?
4. Does the updated app end up quarantined?

## Answers

| # | Answer | Evidence |
|---|---|---|
| 1 | **Yes.** Build 1 → 2 (prod) and 1 → 3 (dev) both downloaded, validated, installed into `/Applications` and relaunched. Old and new were both `Signature=adhoc`, `TeamIdentifier=not set`, with different cdhashes. There was no dialog, no password prompt and no App Management prompt. The negative control (a correct DMG with the wrong EdDSA signature) was **rejected**, so the EdDSA check really is doing the work. | `results/prod.log`, `results/dev.log`, `results/tampered.log`, `results/sparkle-system.log` |
| 2 | **Yes.** Build 3 carries `<sparkle:channel>dev</sparkle:channel>`. A build whose `allowedChannels(for:)` returns `[]` was offered only build 2 and never saw 3. A build returning `["dev"]` was offered 3. | `results/prod.log` (`allowedChannels=[]`, found build 2, then "up to date" with build 3 in the feed), `results/dev.log` |
| 3 | **No: every update triggers a Keychain prompt.** The item's access list names the exact build that created it (an ad-hoc signature's designated requirement is its cdhash), and each update is a new cdhash. The same build reading its own item is fine. | `results/control.log` (same build: read OK), `results/prod.log` and `results/dev.log` (new build: `-25293`), `results/run1-keychain-dialogs-shown/` (real dialogs, see below) |
| 4 | **No quarantine after an update**, but only tested starting from an unquarantined install (the `xattr -dr` route). See the open gaps. | `quarantine: none` on every relaunched build |

### Why question 1 passes: Sparkle's validator

`Sparkle/SUUpdateValidator.m` (2.10.0), in `validateUpdateForHost:…`, accepts an
app-bundle update if **either** check passes:

```objc
// Either DSA must be valid, or Apple Code Signing must be valid.
// We allow failure of one of them, because this allows key rotation without breaking chain of trust.
if (passedDSACheck || passedCodeSigning) { return YES; }
```

Between two ad-hoc builds the code-signing match fails, but the EdDSA check
passes. Two further rules still apply to ad-hoc builds:

- The update must not be *less* signed than the app it replaces (`passesBasicUpdatePolicy…`). Keep signing ad hoc at minimum, and keep `SUPublicEDKey` in every build.
- An update that is code signed must have a valid signature on its own, even if it doesn't match the old one.

The experiment confirms this empirically. The source only explains it.

### The Keychain finding in more detail

Unrot keeps its API key as a login-keychain generic password
(`mac/UnrotMac/Settings/Keychain.swift`). The probe writes an item of the same
shape.

- **Run 1** (`results/run1-keychain-dialogs-shown/`) tried to read without UI using `kSecUseAuthenticationUIFail`. That flag does **not** suppress prompts for a file-based login-keychain item: **real "wants to use your confidential information" dialogs appeared on screen**. Each read blocked for 6–8 s. Build 2's came back `-128` (errSecUserCanceled). Build 3's and the tampered run's came back `0`, meaning someone clicked Allow.
- **Run 2** (`results/*.log`) adds `SecKeychainSetUserInteractionAllowed(false)`, which does turn the UI off. Now a build missing from the access list gets `-25293` (errSecAuthFailed) within 0.01 s, instead of a dialog.

For Unrot, this means that after each auto-update the user is asked once to
allow access to the API key (or has to re-enter it). "Always Allow" adds that
build to the access list, and the next update asks again. **This is the one
thing that makes ad-hoc auto-updating feel broken.** Ways around it, none tested
here:

- **Developer ID signing.** The designated requirement becomes identifier + team, which stays the same across builds, so no prompts. `packaging/release.sh` is ready but has never been run end to end.
- **A stable self-signed code-signing certificate** in CI instead of `-`. The requirement is then tied to that certificate's leaf, which also stays the same across builds. It would need testing: it touches Gatekeeper behaviour and CI keychain setup.
- **Keep the key out of the login keychain**, e.g. a `0600` file under `~/.unrot`. This is weaker at rest.
- The data-protection keychain (`kSecUseDataProtectionKeychain`) is not an option: it needs a keychain-access-groups entitlement, and that needs a Team ID.

## How the experiment works

`SparkleProbe.app` stands in for `Unrot.app`. It's built the way `dmg.sh`
builds Unrot:

- signed ad hoc, inside out
- no hardened runtime
- Sparkle embedded as a framework
- the same bundle-ID-for-both-channels arrangement
- the same kind of Keychain item

Instead of Sparkle's standard window it uses `AutoDriver`, a user driver that
answers "install" to everything and logs each step. Everything after "install"
is Sparkle's own code, the same path the standard UI drives: download, EdDSA and
code-signing validation, DMG extraction, the `Autoupdate` installer, and the
relaunch.

`run.sh` does the following:

1. Makes a throwaway EdDSA key pair as files (`probe/keygen.swift`), so nothing lands in your login keychain.
2. Builds prod 1, prod 2, dev 1 and dev 3.
3. Wraps 2 and 3 in DMGs and signs them with Sparkle's `sign_update --ed-key-file`.
4. Writes an appcast and serves it from `127.0.0.1:8765` with `python3 -m http.server`.
5. For each scenario, copies the old build into `/Applications/SparkleProbe.app`, `open`s it, and waits for the probe to log `DONE`.

| Scenario | Starts as | Appcast | Expect |
|---|---|---|---|
| `control` | prod 1, launched twice | empty | 2nd launch reads the Keychain item without prompting |
| `prod` | prod 1 | 2 (default), 3 (`dev`) | installs 2, which then finds nothing newer |
| `dev` | dev 1 | 2 (default), 3 (`dev`) | installs 3 |
| `tampered` | prod 1 | 2, with build 3's signature | refuses; stays on 1 |

### Running it

You need Xcode's command-line tools, Python 3, network access to download
Sparkle from GitHub (skipped if `SPARKLE_DIR` points at an unpacked release),
and an admin account, so `/Applications` is writable without a password.

```bash
./run.sh
```

```bash
SCENARIOS="prod tampered" ./run.sh
```

It takes about a minute and **won't** pop up dialogs any more. It overwrites
`results/*.log` and `results/appcast.xml`. On exit it removes
`/Applications/SparkleProbe.app` and the `com.unrot.sparkleprobe` Keychain item.
Builds and keys go in a temp dir (or `$WORK`).

## What this means for Unrot

Sparkle is viable **now**, with the Keychain prompt as a known cost until there's
a Developer ID or the key moves. The real implementation would:

- Add Sparkle via SPM. Use `SPUStandardUpdaterController` and a "Check for Updates…" menu item.
- Put `SUFeedURL` and `SUPublicEDKey` in `mac/Support/Info.plist`.
- Implement `allowedChannels(for:)` to return `["dev"]` under `#if DEV_FEATURES`, and `[]` otherwise.
- Keep the EdDSA private key as a GitHub Actions secret.
- In `build-dmg.yml`, after the DMG is built, run `sign_update` (or `generate_appcast`), add an `<item>` (with `<sparkle:channel>dev</sparkle:channel>` for pre-releases) whose enclosure points at the release asset URL, and publish `appcast.xml` somewhere stable over HTTPS, such as GitHub Pages or a branch.
  - **Not** `releases/latest/download/`: that skips pre-releases.
  - `sparkle:version` must be the build number the tag stamps (commit count). It only goes up, which is what Sparkle compares.
- Re-sign Sparkle's helpers ad hoc, inside out, as `build-probe.sh` does. Xcode's "Sign to Run Locally" should do this when the framework is embedded, but that's unverified.

## Open gaps (not verified)

- **Starting from a Gatekeeper-approved install.** Every run started from an unquarantined copy, the state after `xattr -dr com.apple.quarantine`. Users who chose **Open Anyway** instead have a quarantined, approved app. It's untested whether Sparkle can replace it without an App Management prompt, and whether the new build launches cleanly. Testing this needs a person to click Open Anyway.
- **The real `Unrot.app`.** The probe is about 1 MB. Unrot carries a 51 MB frozen Python core in `Resources/unrot-core`, and spawns it as a child process. Two things are untested: that the core is shut down when Sparkle terminates the app for install, and that the bigger bundle still validates and copies cleanly.
- **Sparkle's standard UI** (`SPUStandardUpdaterController`) was not driven. Only the custom `AutoDriver` was. The install path is shared, but the UI isn't.
- **HTTPS and GitHub-hosted downloads.** The feed and DMGs were served over plain HTTP from localhost (allowed by `NSAllowsLocalNetworking`). Redirects from GitHub release asset URLs to their CDN were not exercised.
- **`generate_appcast`** was not tried. The appcast was written by hand around `sign_update` output.
- **Switching channels.** Going prod → dev (one bundle ID, dev replaces prod in `/Applications`) works by the same mechanism, but moving back dev → prod needs a prod build with a higher build number. Commit count normally gives that, but it wasn't tested.
- **Fixes for the Keychain prompt** (self-signed certificate, Developer ID, moving the key) are listed above but none was tried.

## Files

- `probe/main.swift`: the probe app. It logs its signature, quarantine state, Keychain access and every Sparkle step.
- `probe/keygen.swift`: throwaway EdDSA key pair as files.
- `build-probe.sh`: builds and ad-hoc-signs one probe app for a given build number, channel, feed and key.
- `run.sh`: the whole experiment.
- `results/`: logs from the clean unattended run (run 2), plus the appcast it served and Sparkle's own `os_log` lines.
- `results/run1-keychain-dialogs-shown/`: the first run, where Keychain dialogs appeared and were answered by hand. It's kept because it shows the real prompt behaviour.
