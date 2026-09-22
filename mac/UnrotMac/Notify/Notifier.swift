//  Notifier.swift
//  UnrotMac
//
//  Posts the day's notification, if the policy says there is one, and turns
//  its two buttons into exactly the events the window would append.
//
//  Posted by the app, never by the core, so switching notifications off
//  removes a feature rather than breaking a pipeline. Every rule about *when*
//  lives in `NotificationPolicy`; this only asks it, on a clock.

import AppKit
import Observation
import UnrotKit
import UserNotifications

@MainActor
@Observable
final class Notifier: NSObject {

    // Persisted settings. Off by default, deliberately: unrot never asks for
    // notification permission until someone switches this on.
    var enabled: Bool {
        didSet { defaults.set(enabled, forKey: Keys.enabled) }
    }
    var hour: Int {
        didSet { defaults.set(hour, forKey: Keys.hour) }
    }
    /// Whether the single-gap banner carries the comprehension check as a
    /// text reply instead of the two answers. Never the only route to it.
    var carriesCheck: Bool {
        didSet { defaults.set(carriesCheck, forKey: Keys.check) }
    }
    private(set) var permissionDenied = false

    private enum Keys {
        static let enabled = "UnrotNotificationsEnabled"
        static let hour = "UnrotNotificationHour"
        static let check = "UnrotNotificationCarriesCheck"
        static let history = "UnrotNotificationHistory"
    }

    private enum Category {
        static let gap = "unrot.gap"
        static let check = "unrot.check"
        static let digest = "unrot.digest"
    }

    private enum Action {
        static let knew = "unrot.knew"
        static let didNot = "unrot.didnt"
        static let explain = "unrot.explain"
        static let triage = "unrot.triage"
    }

    private let defaults = UserDefaults.standard
    private let store: SurfaceStore
    private let quick: QuickAccept
    private let openMain: () -> Void
    private let openTriage: () -> Void
    private var ticker: Task<Void, Never>?

    init(store: SurfaceStore, quick: QuickAccept, openMain: @escaping () -> Void, openTriage: @escaping () -> Void) {
        self.store = store
        self.quick = quick
        self.openMain = openMain
        self.openTriage = openTriage
        enabled = defaults.bool(forKey: Keys.enabled)
        hour = defaults.object(forKey: Keys.hour) as? Int ?? NotificationPolicy.Settings().hour
        carriesCheck = defaults.bool(forKey: Keys.check)
        super.init()
    }

    func start() {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.setNotificationCategories(Self.categories)
        ticker = Task { [weak self] in
            while !Task.isCancelled {
                await self?.tick()
                // Every ten minutes is plenty to land inside a one-hour window,
                // and costs nothing: while notifications are off this returns
                // before reading anything.
                try? await Task.sleep(for: .seconds(600))
            }
        }
    }

    /// Switching on is the moment to ask for permission, and the only one.
    func setEnabled(_ on: Bool) async {
        guard on else {
            enabled = false
            return
        }
        // `.alert` only: no sound, and no badge -- the menu-bar ring already
        // says how many are waiting, and it is never a red dot.
        let granted = (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert])) ?? false
        permissionDenied = !granted
        enabled = granted
    }

    // MARK: - The clock

    private var history: NotificationPolicy.History {
        get {
            guard let data = defaults.data(forKey: Keys.history),
                  let stored = try? JSONDecoder().decode(StoredHistory.self, from: data)
            else { return .init() }
            return .init(lastDeliveredDay: stored.day, notified: Set(stored.notified))
        }
        set {
            let stored = StoredHistory(day: newValue.lastDeliveredDay, notified: Array(newValue.notified))
            defaults.set(try? JSONEncoder().encode(stored), forKey: Keys.history)
        }
    }

    private struct StoredHistory: Codable {
        var day: String?
        var notified: [String]
    }

    func tick(now: Date = Date()) async {
        let settings = NotificationPolicy.Settings(enabled: enabled, hour: hour)
        guard settings.enabled else { return }
        guard Calendar.current.component(.hour, from: now) == hour else { return }

        await store.load()
        let decision = NotificationPolicy.decide(
            now: now,
            settings: settings,
            history: history,
            surface: store.state == .failed ? nil : store.surface,
            sessionLive: ClaudeActivity.isLive(now: now)
        )
        guard await post(decision) else { return }
        history = NotificationPolicy.record(decision, at: now, into: history)
    }

    private func post(_ decision: NotificationPolicy.Decision) async -> Bool {
        let content = UNMutableNotificationContent()
        // Passive: no sound, no time-sensitive override, nothing that breaks
        // through a Focus mode.
        content.interruptionLevel = .passive
        content.threadIdentifier = "unrot"

        switch decision {
        case .quiet:
            return false

        case .single(let gap):
            content.title = gap.name
            content.userInfo = ["conceptId": gap.conceptId, "encounterId": gap.encounterId]
            if carriesCheck, let check = try? await store.check(conceptId: gap.conceptId) {
                // The question exactly as the server words it, because the
                // answer is stored against that wording.
                content.body = check.promptText
                content.categoryIdentifier = Category.check
            } else {
                content.body = gap.paraphrase ?? "Leaned on in a session, and waved through."
                content.categoryIdentifier = Category.gap
            }

        case .digest(let gaps):
            content.title = "\(gaps.count) things waiting on you"
            content.body = NotificationPolicy.list(gaps.map(\.name))
            content.categoryIdentifier = Category.digest
            content.userInfo = ["digest": true]
        }

        let request = UNNotificationRequest(identifier: "unrot.daily", content: content, trigger: nil)
        do {
            try await UNUserNotificationCenter.current().add(request)
            return true
        } catch {
            return false
        }
    }

    // MARK: - Categories

    /// Two actions, never three. macOS collapses a notification's actions into
    /// a menu past two, and this product has exactly two answers -- so the
    /// platform limit and the product agree. The check is a separate category
    /// rather than a third button.
    private static var categories: Set<UNNotificationCategory> {
        let knew = UNNotificationAction(identifier: Action.knew, title: "I knew this")
        let didNot = UNNotificationAction(identifier: Action.didNot, title: "I didn't know this")
        let explain = UNTextInputNotificationAction(
            identifier: Action.explain,
            title: "Explain it",
            textInputButtonTitle: "Send",
            textInputPlaceholder: "In a line or two…"
        )
        let triage = UNNotificationAction(identifier: Action.triage, title: "Triage", options: [.foreground])
        return [
            UNNotificationCategory(identifier: Category.gap, actions: [didNot, knew], intentIdentifiers: []),
            UNNotificationCategory(identifier: Category.check, actions: [explain], intentIdentifiers: []),
            UNNotificationCategory(identifier: Category.digest, actions: [triage], intentIdentifiers: []),
        ]
    }

    // MARK: - Answers

    fileprivate func handle(action: String, conceptId: String?, encounterId: String?, text: String?, digest: Bool) async {
        switch action {
        case Action.knew, Action.didNot:
            guard let conceptId, let encounterId else { return }
            await store.load()
            // Answered somewhere else since the banner went out: nothing to do,
            // and certainly nothing to answer twice.
            guard let concept = store.surface?.concepts.first(where: { $0.conceptId == conceptId }),
                  let encounter = concept.encounters.first(where: { $0.encounterId == encounterId && $0.judgment == nil })
            else { return }
            // Through quick accept, so a notification answer appends exactly the
            // event the window would, and gets the same undo.
            await quick.answer(concept: concept, encounter: encounter, action == Action.didNot ? .confirm : .dismiss)

        case Action.explain:
            guard let conceptId, let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
            // Stored before it is graded, server-side, so a grader that is down
            // costs the level and never the words.
            _ = await store.submitExplanation(conceptId: conceptId, text: text)

        case Action.triage:
            openTriage()

        case UNNotificationDefaultActionIdentifier:
            if digest { openTriage() } else { openMain() }

        default:
            break
        }
    }
}

extension Notifier: UNUserNotificationCenterDelegate {
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        // Pull the plain values out before crossing to the main actor; the
        // response itself is not Sendable.
        let info = response.notification.request.content.userInfo
        let action = response.actionIdentifier
        let conceptId = info["conceptId"] as? String
        let encounterId = info["encounterId"] as? String
        let digest = info["digest"] as? Bool ?? false
        let text = (response as? UNTextInputNotificationResponse)?.userText
        await handle(action: action, conceptId: conceptId, encounterId: encounterId, text: text, digest: digest)
    }

    /// While unrot is frontmost the list is already in front of you, so the
    /// banner is not shown -- it goes to Notification Center quietly instead.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.list]
    }
}
