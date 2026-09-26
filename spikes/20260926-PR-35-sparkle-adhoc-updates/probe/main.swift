// SparkleProbe: a stand-in for Unrot.app that updates itself with Sparkle and
// writes down everything that matters to a log file, so the experiment can run
// without anyone clicking anything.
//
// Built like Unrot.app is by packaging/dmg.sh: signed ad hoc, no hardened
// runtime, an embedded framework, and an API key kept in the login Keychain as
// a plain generic password (same shape as mac/UnrotMac/Settings/Keychain.swift).
//
// The standard Sparkle UI is replaced by AutoDriver, which says yes to every
// question. Everything after the user's "Install" -- download, EdDSA and code
// signing validation, extraction, the Autoupdate installer, relaunch -- is
// Sparkle's own code, identical to what the standard UI would drive.

import AppKit
import Security
import Sparkle

let info = Bundle.main.infoDictionary ?? [:]
let version = info["CFBundleVersion"] as? String ?? "?"
let logPath = info["ProbeLogPath"] as? String ?? "/tmp/sparkle-probe.log"

func log(_ message: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    let line = "\(stamp) build=\(version) pid=\(getpid()) \(message)\n"
    FileHandle.standardError.write(Data(line.utf8))
    if let handle = FileHandle(forWritingAtPath: logPath) {
        handle.seekToEndOfFile()
        handle.write(Data(line.utf8))
        try? handle.close()
    } else {
        FileManager.default.createFile(atPath: logPath, contents: Data(line.utf8))
    }
}

/// Set once the Keychain probe has an answer. `finish` waits for it, so a build
/// that quits quickly still records what the Keychain said.
var keychainAnswered = false

func finish(_ why: String, waited: Int = 0) {
    if !keychainAnswered && waited < 40 {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { finish(why, waited: waited + 1) }
        return
    }
    if !keychainAnswered { log("keychain: NO ANSWER after 20s -- the read is blocked, most likely on a dialog") }
    log("DONE \(why)")
    DispatchQueue.main.async { NSApp.terminate(nil) }
}

// MARK: - What this build is

func describeSignature() {
    var code: SecStaticCode?
    guard SecStaticCodeCreateWithPath(Bundle.main.bundleURL as CFURL, [], &code) == errSecSuccess, let code else {
        log("signature: could not read")
        return
    }
    var cfInfo: CFDictionary?
    SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation), &cfInfo)
    let info = cfInfo as? [String: Any] ?? [:]
    let cdhash = (info[kSecCodeInfoUnique as String] as? Data)?.map { String(format: "%02x", $0) }.joined() ?? "none"
    let team = info[kSecCodeInfoTeamIdentifier as String] as? String ?? "none"
    let flags = (info[kSecCodeInfoFlags as String] as? UInt32) ?? 0
    let adhoc = flags & 0x2 != 0  // kSecCodeSignatureAdhoc
    log("signature: cdhash=\(cdhash) team=\(team) adhoc=\(adhoc)")

    let valid = SecStaticCodeCheckValidity(code, SecCSFlags(rawValue: kSecCSCheckAllArchitectures), nil)
    log("signature: valid=\(valid == errSecSuccess) status=\(valid)")
}

func describeQuarantine() {
    let path = Bundle.main.bundlePath
    let size = getxattr(path, "com.apple.quarantine", nil, 0, 0, 0)
    if size < 0 {
        log("quarantine: none on \(path)")
    } else {
        var buffer = [UInt8](repeating: 0, count: size)
        getxattr(path, "com.apple.quarantine", &buffer, size, 0, 0)
        log("quarantine: PRESENT on \(path): \(String(decoding: buffer, as: UTF8.self))")
    }
}

// MARK: - Keychain, the way Unrot keeps its API key

let keychainService = "com.unrot.sparkleprobe"
let keychainAccount = "probe-api-key"

/// Reads the item without allowing any UI. If this build is not on the item's
/// access list, macOS would put up "wants to use your confidential
/// information"; with UI forbidden that shows up as errSecInteractionNotAllowed
/// instead of a dialog, so the experiment can run unattended.
///
/// kSecUseAuthenticationUIFail alone is NOT enough for a login-keychain item
/// like this one: the first run of this spike used only that, and real dialogs
/// appeared (results/run1-keychain-dialogs-shown). The legacy, process-wide
/// switch below is what actually turns them off for the file-based keychain.
func probeKeychain() {
    SecKeychainSetUserInteractionAllowed(false)
    var query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: keychainService,
        kSecAttrAccount as String: keychainAccount,
        kSecReturnData as String: true,
        kSecMatchLimit as String: kSecMatchLimitOne,
        kSecUseAuthenticationUI as String: kSecUseAuthenticationUIFail,
    ]
    var result: AnyObject?
    let started = Date()
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    defer { DispatchQueue.main.async { keychainAnswered = true } }
    log("keychain: SecItemCopyMatching returned \(status) after \(String(format: "%.2f", Date().timeIntervalSince(started)))s")
    switch status {
    case errSecSuccess:
        let value = (result as? Data).map { String(decoding: $0, as: UTF8.self) } ?? "?"
        log("keychain: read OK without prompting, value=\(value)")
    case errSecItemNotFound:
        query.removeValue(forKey: kSecReturnData as String)
        query.removeValue(forKey: kSecMatchLimit as String)
        query.removeValue(forKey: kSecUseAuthenticationUI as String)
        SecKeychainSetUserInteractionAllowed(true)
        query[kSecValueData as String] = Data("written-by-build-\(version)".utf8)
        let added = SecItemAdd(query as CFDictionary, nil)
        log("keychain: no item yet, created it, status=\(added)")
    case errSecInteractionNotAllowed, errSecAuthFailed:
        // With interaction switched off, a build missing from the item's
        // access list gets errSecAuthFailed straight away; with it on, that is
        // the "wants to use your confidential information" dialog.
        log("keychain: WOULD PROMPT (status \(status)): this build is not on the item's access list")
    default:
        log("keychain: read failed status=\(status)")
    }
}

// MARK: - Sparkle

final class Delegate: NSObject, SPUUpdaterDelegate {
    func allowedChannels(for updater: SPUUpdater) -> Set<String> {
        let raw = info["ProbeChannels"] as? String ?? ""
        let channels = Set(raw.split(separator: ",").map(String.init))
        log("sparkle: allowedChannels=\(channels.sorted())")
        return channels
    }

    func updater(_ updater: SPUUpdater, didFinishLoading appcast: SUAppcast) {
        let items = appcast.items.map { "\($0.versionString)[\($0.channel ?? "default")]" }
        log("sparkle: appcast loaded, items=\(items)")
    }

    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        log("sparkle: found update build=\(item.versionString) channel=\(item.channel ?? "default")")
    }

    func updaterDidNotFindUpdate(_ updater: SPUUpdater, error: Error) {
        log("sparkle: no update found (\((error as NSError).localizedDescription))")
    }

    func updater(_ updater: SPUUpdater, willInstallUpdate item: SUAppcastItem) {
        log("sparkle: will install build=\(item.versionString)")
    }

    func updaterWillRelaunchApplication(_ updater: SPUUpdater) {
        log("sparkle: will relaunch")
    }

    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        let ns = error as NSError
        let underlying = (ns.userInfo[NSUnderlyingErrorKey] as? NSError).map { " underlying=\($0.domain)#\($0.code) \($0.localizedDescription)" } ?? ""
        log("sparkle: ABORTED \(ns.domain)#\(ns.code) \(ns.localizedDescription)\(underlying)")
    }
}

/// Says yes to everything, logs what it was asked, and quits the app when there
/// is nothing more to do. Sparkle calls it on the main thread.
final class AutoDriver: NSObject, SPUUserDriver {
    func show(_ request: SPUUpdatePermissionRequest, reply: @escaping (SUUpdatePermissionResponse) -> Void) {
        log("driver: permission request -> no automatic checks")
        reply(SUUpdatePermissionResponse(automaticUpdateChecks: false, sendSystemProfile: false))
    }

    func showUserInitiatedUpdateCheck(cancellation: @escaping () -> Void) {
        log("driver: checking")
    }

    func showUpdateFound(with appcastItem: SUAppcastItem, state: SPUUserUpdateState, reply: @escaping (SPUUserUpdateChoice) -> Void) {
        log("driver: update found build=\(appcastItem.versionString) -> install")
        reply(.install)
    }

    func showUpdateReleaseNotes(with downloadData: SPUDownloadData) {}
    func showUpdateReleaseNotesFailedToDownloadWithError(_ error: Error) {}

    func showUpdateNotFoundWithError(_ error: Error, acknowledgement: @escaping () -> Void) {
        acknowledgement()
        finish("no newer update for this build")
    }

    func showUpdaterError(_ error: Error, acknowledgement: @escaping () -> Void) {
        log("driver: ERROR \((error as NSError).localizedDescription)")
        acknowledgement()
        finish("updater error")
    }

    func showDownloadInitiated(cancellation: @escaping () -> Void) { log("driver: downloading") }
    func showDownloadDidReceiveExpectedContentLength(_ expectedContentLength: UInt64) {}
    func showDownloadDidReceiveData(ofLength length: UInt64) {}
    func showDownloadDidStartExtractingUpdate() { log("driver: extracting") }
    func showExtractionReceivedProgress(_ progress: Double) {}

    func showReady(toInstallAndRelaunch reply: @escaping (SPUUserUpdateChoice) -> Void) {
        log("driver: ready to install -> install and relaunch")
        reply(.install)
    }

    func showInstallingUpdate(withApplicationTerminated applicationTerminated: Bool, retryTerminatingApplication: @escaping () -> Void) {
        log("driver: installing (terminated=\(applicationTerminated))")
    }

    func showUpdateInstalledAndRelaunched(_ relaunched: Bool, acknowledgement: @escaping () -> Void) {
        log("driver: installed, relaunched=\(relaunched)")
        acknowledgement()
    }

    func showUpdateInFocus() {}
    func dismissUpdateInstallation() {}
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    let delegate = Delegate()
    let driver = AutoDriver()
    var updater: SPUUpdater!

    func applicationDidFinishLaunching(_ notification: Notification) {
        log("launched from \(Bundle.main.bundlePath)")
        describeSignature()
        describeQuarantine()
        // Off the main thread: if macOS put up a dialog despite being told not
        // to, the log would be missing its keychain line rather than Sparkle
        // stalling behind it.
        DispatchQueue.global().async { probeKeychain() }

        updater = SPUUpdater(hostBundle: .main, applicationBundle: .main, userDriver: driver, delegate: delegate)
        do {
            try updater.start()
        } catch {
            log("sparkle: could not start: \(error)")
            finish("updater did not start")
            return
        }
        updater.checkForUpdates()

        // Never leave a probe running if something hangs.
        DispatchQueue.main.asyncAfter(deadline: .now() + 90) { finish("timed out") }
    }
}

let app = NSApplication.shared
let appDelegate = AppDelegate()
app.delegate = appDelegate
app.setActivationPolicy(.accessory)
app.run()
