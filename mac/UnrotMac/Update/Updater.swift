//  Updater.swift
//  UnrotMac
//
//  Self-updating, with Sparkle, from the appcast every GitHub release adds
//  itself to (build-dmg.yml). The PR-35 spike showed Sparkle installs an update
//  between two ad-hoc-signed builds on its EdDSA signature alone, which is why
//  this works before there is a Developer ID.
//
//  Two channels, kept apart: every appcast item is tagged `dev` or `prod`, and
//  a build only accepts items tagged with its own UnrotChannel. A tester on a
//  dev build is never moved onto a prod build that would take their dev
//  features away, and a prod user never sees a dev build.
//
//  Off in three cases, each with a reason the UI can show:
//    - a Debug build, which would otherwise replace itself in DerivedData
//      with a downloaded Release build;
//    - a build without SUFeedURL or SUPublicEDKey;
//    - a build whose channel is not dev or prod.

import Foundation
import Observation
import Sparkle

@MainActor
@Observable
final class Updater {
    /// Why this build does not update itself, or nil when it does.
    let disabledReason: String?

    /// Sparkle's own flag: false while a check or an update is already under
    /// way, when the menu item should be disabled.
    private(set) var canCheck = false

    /// Mirrors Sparkle's setting, which it keeps in the app's user defaults.
    var checksAutomatically: Bool {
        didSet { controller?.updater.automaticallyChecksForUpdates = checksAutomatically }
    }

    /// When Sparkle last checked, by itself or when asked.
    private(set) var lastChecked: Date?

    let channel: String

    @ObservationIgnored private var controller: SPUStandardUpdaterController?
    @ObservationIgnored private let channels: Channels
    @ObservationIgnored private var observation: NSKeyValueObservation?

    init(bundle: Bundle = .main) {
        let channel = bundle.object(forInfoDictionaryKey: "UnrotChannel") as? String ?? ""
        self.channel = channel
        channels = Channels(channel: channel)
        disabledReason = Self.disabledReason(bundle: bundle, channel: channel)
        checksAutomatically = false
    }

    #if DEBUG
    /// For the snapshot run: an updater that looks switched on and never starts.
    static func preview(channel: String = "prod") -> Updater {
        Updater(channel: channel, lastChecked: .now.addingTimeInterval(-3 * 3600))
    }

    private init(channel: String, lastChecked: Date) {
        self.channel = channel
        channels = Channels(channel: channel)
        disabledReason = nil
        checksAutomatically = true
        canCheck = true
        self.lastChecked = lastChecked
    }
    #endif

    /// Starts Sparkle, which schedules its own checks from here on. Separate
    /// from init so the Debug snapshot run, which returns before anything else
    /// starts, never touches it.
    func start() {
        guard disabledReason == nil, controller == nil else { return }
        let controller = SPUStandardUpdaterController(
            startingUpdater: true,
            updaterDelegate: channels,
            userDriverDelegate: nil
        )
        self.controller = controller
        let updater = controller.updater
        checksAutomatically = updater.automaticallyChecksForUpdates
        lastChecked = updater.lastUpdateCheckDate
        // Sparkle changes canCheckForUpdates on the main thread, so the KVO
        // callback arrives there too.
        observation = updater.observe(\.canCheckForUpdates, options: [.initial, .new]) { [weak self] updater, _ in
            MainActor.assumeIsolated {
                self?.canCheck = updater.canCheckForUpdates
                self?.lastChecked = updater.lastUpdateCheckDate
            }
        }
    }

    /// Checks now, with Sparkle's window saying what it found, including "you're
    /// up to date".
    func checkForUpdates() {
        controller?.checkForUpdates(nil)
    }

    private static func disabledReason(bundle: Bundle, channel: String) -> String? {
        #if DEBUG
        return "Debug builds do not update themselves."
        #else
        func value(_ key: String) -> String {
            (bundle.object(forInfoDictionaryKey: key) as? String ?? "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if value("SUFeedURL").isEmpty || value("SUPublicEDKey").isEmpty {
            return "This build was made without an update feed or signing key."
        }
        guard ["dev", "prod"].contains(channel) else {
            return "This build's channel (\(channel.isEmpty ? "none" : channel)) has no updates."
        }
        return nil
        #endif
    }
}

/// The updater delegate: only this build's channel. Sparkle always offers items
/// with no channel as well, so every item the appcast carries has one
/// (packaging/appcast.py).
private final class Channels: NSObject, SPUUpdaterDelegate {
    let channel: String

    init(channel: String) { self.channel = channel }

    func allowedChannels(for updater: SPUUpdater) -> Set<String> {
        [channel]
    }
}
