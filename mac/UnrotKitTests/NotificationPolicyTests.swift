//  NotificationPolicyTests.swift
//  UnrotKitTests
//
//  Every rule that keeps notifications from becoming the nagging version.

import Foundation
import Testing

@testable import UnrotKit

@Suite("When a notification is allowed")
struct NotificationPolicyTests {

    private var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/London")!
        return calendar
    }

    private func at(_ hour: Int, day: Int = 22) -> Date {
        calendar.date(from: DateComponents(year: 2026, month: 9, day: day, hour: hour, minute: 10))!
    }

    private let on = NotificationPolicy.Settings(enabled: true, hour: 17)

    private func surface(open: [String], bucket: String = "open") throws -> Surface {
        let concepts = open.enumerated().map { index, name in
            #"""
            {"concept_id":"c\#(index)","name":"\#(name)","gap_type":"term","state":"gap","bucket":"\#(bucket)",
             "aliases":[],"encounter_count":1,"unjudged":1,"first_seen_at":null,"last_seen_at":null,
             "latest_level":null,"explanations":[],"material":[],
             "encounters":[{"encounter_id":"e\#(index)","source":"transcript","paraphrase":"p\#(index)",
               "judgment":null,"judged_at":null,"occurred_at":"2026-09-01","detector_version":null,
               "session_id":null,"line_start":null,"line_end":null,"resolvable":false}]}
            """#
        }.joined(separator: ",")
        let json = #"""
        {"state":"gaps","headline":"H","detail":"D",
         "capture":{"sessions":3,"human_turns":9,"last_activity":null,"sessions_analysed":3,"sessions_clean":1},
         "counts":{"open":\#(bucket == "open" ? open.count : 0),"learning":0,"closed":0},"fixtures":0,
         "concepts":[\#(concepts)]}
        """#
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(Surface.self, from: Data(json.utf8))
    }

    private func decide(
        _ now: Date,
        settings: NotificationPolicy.Settings? = nil,
        history: NotificationPolicy.History = .init(),
        surface: Surface?,
        live: Bool = false
    ) -> NotificationPolicy.Decision {
        NotificationPolicy.decide(
            now: now, calendar: calendar, settings: settings ?? on,
            history: history, surface: surface, sessionLive: live
        )
    }

    @Test("off unless switched on")
    func offByDefault() throws {
        #expect(NotificationPolicy.Settings().enabled == false)
        #expect(decide(at(17), settings: .init(), surface: try surface(open: ["Backpressure"])) == .quiet(.disabled))
    }

    @Test("only in the hour the user picked")
    func onlyInTheWindow() throws {
        let one = try surface(open: ["Backpressure"])
        #expect(decide(at(9), surface: one) == .quiet(.outsideWindow))
        #expect(decide(at(18), surface: one) == .quiet(.outsideWindow))
        #expect(decide(at(17), surface: one) != .quiet(.outsideWindow))
    }

    @Test("one gap gets the specific banner; several get one digest")
    func singleOrDigest() throws {
        if case .single(let gap) = decide(at(17), surface: try surface(open: ["Backpressure"])) {
            #expect(gap.name == "Backpressure")
            #expect(gap.encounterId == "e0")
        } else {
            Issue.record("one waiting gap should be a single banner")
        }
        if case .digest(let gaps) = decide(at(17), surface: try surface(open: ["A", "B", "C"])) {
            #expect(gaps.map(\.name) == ["A", "B", "C"])
        } else {
            Issue.record("three waiting gaps should be one digest")
        }
    }

    @Test("never on a clean day")
    func neverOnACleanDay() throws {
        #expect(decide(at(17), surface: try surface(open: [])) == .quiet(.nothingNew))
        // Concepts that are learning or closed are progress, and progress is
        // never a notification.
        #expect(decide(at(17), surface: try surface(open: ["Known"], bucket: "closed")) == .quiet(.nothingNew))
    }

    @Test("at most once a day")
    func onceADay() throws {
        let decision = decide(at(17), surface: try surface(open: ["A"]))
        let after = NotificationPolicy.record(decision, at: at(17), calendar: calendar, into: .init())

        #expect(decide(at(17), history: after, surface: try surface(open: ["A", "B"])) == .quiet(.alreadyToday))
    }

    @Test("never twice for the same gap, even on a later day")
    func neverTwice() throws {
        let decision = decide(at(17, day: 22), surface: try surface(open: ["A"]))
        let after = NotificationPolicy.record(decision, at: at(17, day: 22), calendar: calendar, into: .init())

        // Next day, A is still waiting and nothing else is: silence.
        #expect(decide(at(17, day: 23), history: after, surface: try surface(open: ["A"])) == .quiet(.nothingNew))
        // Next day, B has arrived: B alone, not A again.
        if case .single(let gap) = decide(at(17, day: 23), history: after, surface: try surface(open: ["A", "B"])) {
            #expect(gap.name == "B")
        } else {
            Issue.record("only the new gap should be mentioned")
        }
    }

    @Test("never while a Claude Code session is live")
    func notMidSession() throws {
        #expect(decide(at(17), surface: try surface(open: ["A"]), live: true) == .quiet(.sessionLive))
    }

    @Test("never on a failure")
    func notOnAFailure() {
        #expect(decide(at(17), surface: nil) == .quiet(.noSurface))
    }

    @Test("a quiet decision records nothing")
    func quietIsNotADelivery() {
        let after = NotificationPolicy.record(.quiet(.nothingNew), at: at(17), calendar: calendar, into: .init())
        #expect(after == .init())
    }

    @Test("names read as a sentence")
    func names() {
        #expect(NotificationPolicy.list(["A"]) == "A")
        #expect(NotificationPolicy.list(["A", "B"]) == "A and B")
        #expect(NotificationPolicy.list(["A", "B", "C"]) == "A, B and C")
    }
}
