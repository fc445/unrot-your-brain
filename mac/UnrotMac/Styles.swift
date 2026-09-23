//  Styles.swift
//  UnrotMac
//
//  The handful of pieces every screen on the design canvas is built from: two
//  button weights and a link, a pill, a keycap, a small-caps label, and dates
//  the way the canvas writes them. One place, so the window, the popover and
//  the panels cannot drift into four slightly different apps.

import SwiftUI
import UnrotKit

extension Color {
    /// The canvas's link and accent amber: `#8a5208` on paper, lighter at night.
    static let link = Color.ink(0x8a5208, 0xe5a94f)
    static let watching = Color.ink(0x2f7a4f, 0x72b98d)
}

/// Primary is the one thing the screen is asking; secondary is its alternative.
struct UnrotButton: ButtonStyle {
    enum Weight { case primary, secondary }
    var weight: Weight = .secondary
    var fill = false

    func makeBody(configuration: Configuration) -> some View {
        Rendered(configuration: configuration, weight: weight, fill: fill)
    }

    /// A view rather than a modifier chain, because only a view can read
    /// `isEnabled` -- and a disabled button that looks enabled is a button
    /// people click and conclude is broken.
    private struct Rendered: View {
        let configuration: Configuration
        let weight: Weight
        let fill: Bool
        @Environment(\.isEnabled) private var isEnabled

        var body: some View {
            label.opacity(isEnabled ? 1 : 0.4)
        }

        private var label: some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .frame(maxWidth: fill ? .infinity : nil)
            .foregroundStyle(weight == .primary ? Color.paper : Color.inkPrimary)
            .background(
                RoundedRectangle(cornerRadius: 7)
                    .fill(weight == .primary ? Color.inkPrimary : Color.card)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 7)
                    .stroke(weight == .primary ? Color.clear : Color.ruleStrong, lineWidth: 1)
            )
            .opacity(configuration.isPressed ? 0.75 : 1)
            .contentShape(RoundedRectangle(cornerRadius: 7))
        }
    }
}

/// "Show the moment →" -- amber, never blue: blue on this palette reads as the
/// learning bucket, and a link is not a bucket.
struct LinkButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(Color.link)
            .opacity(configuration.isPressed ? 0.6 : 1)
            .contentShape(Rectangle())
    }
}

struct Pip: View {
    let text: String
    let tint: Color
    let wash: Color

    var body: some View {
        Text(text)
            .font(.system(size: 10.5, weight: .semibold))
            .foregroundStyle(tint)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(wash, in: Capsule())
    }
}

/// Small capitals above a group: "LISTS", "THE CHECK", "WHAT YOU SELECTED".
struct Eyebrow: View {
    let text: String
    var tint: Color = .inkFaint

    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 10.5, weight: .semibold))
            .tracking(0.6)
            .foregroundStyle(tint)
    }
}

struct Keycap: View {
    let key: String
    var inverted = false

    var body: some View {
        Text(key)
            .font(.system(size: 10, weight: .semibold, design: .monospaced))
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .foregroundStyle(inverted ? Color.paper.opacity(0.85) : Color.inkSoft)
            .background(
                RoundedRectangle(cornerRadius: 3)
                    .fill(inverted ? Color.paper.opacity(0.15) : Color.sunk)
            )
            .overlay(RoundedRectangle(cornerRadius: 3).stroke(Color.rule, lineWidth: inverted ? 0 : 1))
    }
}

/// "● Watching", "● Paused", "● Analysing 2 of 5". The app's one ambient status.
struct StatusPill: View {
    let watcher: Watcher
    let core: CoreProcess

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(tint).frame(width: 7, height: 7)
            Text(label)
        }
        .font(.system(size: 12, weight: .semibold))
        .foregroundStyle(tint)
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
        .background(tint.opacity(0.12), in: Capsule())
    }

    private var label: String {
        if !core.status.isUp { return "Core not running" }
        if let progress = watcher.progress { return "Analysing \(progress.done + 1) of \(progress.total)" }
        if !watcher.started { return "Not watching yet" }
        return watcher.paused ? "Paused" : "Watching"
    }

    private var tint: Color {
        if !core.status.isUp { return .alarm }
        if watcher.isRunning { return .bucketOpen }
        return watcher.paused || !watcher.started ? .inkFaint : .watching
    }
}

enum Dates {
    static func parse(_ text: String?) -> Date? {
        guard let text else { return nil }
        // Value-type format styles are Sendable; the old formatter classes are not.
        return (try? Date(text, strategy: Date.ISO8601FormatStyle(includingFractionalSeconds: true)))
            ?? (try? Date(text, strategy: .iso8601))
    }

    /// "19 Sept 16:04", the canvas's provenance style.
    static func stamp(_ text: String?) -> String? {
        guard let date = parse(text) else { return nil }
        // Day, month and time without the locale's "at": the canvas's form.
        let day = date.formatted(.dateTime.day().month(.abbreviated))
        let time = date.formatted(.dateTime.hour(.twoDigits(amPM: .omitted)).minute())
        return "\(day) \(time)"
    }

    /// "Thursday 19:42" within the week, "19 Sept" beyond it.
    static func recent(_ text: String?) -> String? {
        guard let date = parse(text) else { return nil }
        if Date().timeIntervalSince(date) < 6 * 86_400 {
            return date.formatted(.dateTime.weekday(.wide).hour().minute())
        }
        return date.formatted(.dateTime.day().month(.abbreviated))
    }
}

extension Concept {
    /// The encounter a card speaks for: the newest one, which is what the
    /// server orders first.
    var lead: Encounter? { encounters.first }

    /// The one a judgment should land on. One answer settles the concept
    /// (read.py's `bucket_for`), so the first unanswered one is enough.
    var unanswered: Encounter? { encounters.first { $0.judgment == nil } }

    var seen: String {
        switch encounterCount {
        case 1: "seen once"
        case 2: "seen twice"
        default: "seen \(encounterCount) times"
        }
    }

    var typedIn: Bool { encounters.contains { $0.source == "manual" } }
}

extension Encounter {
    /// "payments-api · 19 Sept 16:04 · lines 214–231", omitting what is unknown.
    var provenance: String? {
        guard sessionId != nil else { return nil }
        var parts: [String] = []
        if let repo { parts.append(repo) }
        if let stamp = Dates.stamp(occurredAt) { parts.append(stamp) }
        if let start = lineStart, let end = lineEnd {
            parts.append(start == end ? "line \(start)" : "lines \(start)–\(end)")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}
