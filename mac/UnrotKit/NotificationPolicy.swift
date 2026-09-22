//  NotificationPolicy.swift
//  UnrotKit
//
//  Whether to send a notification, and which one. A pure function, so every
//  rule is a test rather than a hope.
//
//  The tension this resolves: journey 38 says the nagging version gets
//  uninstalled and the ambient version gets used, and ideas.md §5 is blunter --
//  a daily list of your failures is a product people delete in week two. So
//  notifications are off by default and almost never sent:
//
//  * one delivery a day at most, in an hour the user picks;
//  * never at the moment of detection -- this only ever runs against the clock;
//  * never on a clean day, never while a Claude Code session is live;
//  * never twice for the same gap;
//  * never for material, grades or progress -- this only looks at `open`.
//
//  Delivery is the app's job, never the core's, so switching notifications off
//  removes a feature rather than breaking a pipeline. What counts as waiting is
//  still the server's decision: this reads the `open` bucket as sent.

import Foundation

public enum NotificationPolicy {

    public struct Settings: Equatable, Sendable {
        public var enabled: Bool
        /// The hour (0–23) in which the day's one delivery may happen.
        public var hour: Int

        public init(enabled: Bool = false, hour: Int = 17) {
            self.enabled = enabled
            self.hour = hour
        }
    }

    public struct History: Equatable, Sendable {
        /// `yyyy-MM-dd` of the last delivery, in the user's calendar.
        public var lastDeliveredDay: String?
        /// Concepts already notified about, ever. "Never twice for the same gap"
        /// is taken at its word: a gap met again is still the same gap.
        public var notified: Set<String>

        public init(lastDeliveredDay: String? = nil, notified: Set<String> = []) {
            self.lastDeliveredDay = lastDeliveredDay
            self.notified = notified
        }
    }

    public enum Quiet: Equatable, Sendable {
        case disabled
        /// No surface to read. A notification is never sent on a failure.
        case noSurface
        case outsideWindow
        case alreadyToday
        case sessionLive
        /// Nothing waiting that has not already been mentioned -- which is also
        /// what a clean day looks like.
        case nothingNew
    }

    public struct Gap: Equatable, Sendable {
        public let conceptId: String
        public let name: String
        public let encounterId: String
        public let paraphrase: String?
    }

    public enum Decision: Equatable, Sendable {
        case quiet(Quiet)
        /// One thing waiting: the specific banner, with the two answers on it.
        case single(Gap)
        /// Several: one digest rather than several banners.
        case digest([Gap])
    }

    public static func decide(
        now: Date,
        calendar: Calendar = .current,
        settings: Settings,
        history: History,
        surface: Surface?,
        sessionLive: Bool
    ) -> Decision {
        guard settings.enabled else { return .quiet(.disabled) }
        guard let surface else { return .quiet(.noSurface) }
        guard calendar.component(.hour, from: now) == settings.hour else { return .quiet(.outsideWindow) }
        guard history.lastDeliveredDay != day(now, calendar) else { return .quiet(.alreadyToday) }
        guard !sessionLive else { return .quiet(.sessionLive) }

        let fresh: [Gap] = surface.concepts
            .filter { $0.bucket == .open && !history.notified.contains($0.conceptId) }
            .compactMap { concept in
                guard let encounter = concept.encounters.first(where: { $0.judgment == nil }) else { return nil }
                return Gap(
                    conceptId: concept.conceptId,
                    name: concept.name,
                    encounterId: encounter.encounterId,
                    paraphrase: encounter.paraphrase
                )
            }

        switch fresh.count {
        case 0: return .quiet(.nothingNew)
        case 1: return .single(fresh[0])
        default: return .digest(fresh)
        }
    }

    /// The history after a delivery.
    public static func record(_ decision: Decision, at now: Date, calendar: Calendar = .current, into history: History) -> History {
        var next = history
        switch decision {
        case .quiet:
            return history
        case .single(let gap):
            next.notified.insert(gap.conceptId)
        case .digest(let gaps):
            next.notified.formUnion(gaps.map(\.conceptId))
        }
        next.lastDeliveredDay = day(now, calendar)
        return next
    }

    public static func day(_ date: Date, _ calendar: Calendar) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    /// "Git Rebase, Backpressure and Walking Skeleton".
    public static func list(_ names: [String]) -> String {
        switch names.count {
        case 0: return ""
        case 1: return names[0]
        default: return names.dropLast().joined(separator: ", ") + " and " + names.last!
        }
    }
}
