//  WatchPolicyTests.swift
//  UnrotKitTests

import Foundation
import Testing

@testable import UnrotKit

@Suite("When a session is ready to capture")
struct WatchPolicyTests {
    private let now = Date(timeIntervalSince1970: 1_000_000)
    private func ago(_ minutes: Double) -> Date { now.addingTimeInterval(-minutes * 60) }

    @Test("a session is taken once it has been quiet for the whole period")
    func quietPeriod() {
        let changes = ["/p/repo/a.jsonl": ago(11)]
        #expect(WatchPolicy.ready(changes: changes, now: now) == ["/p/repo/a.jsonl"])
        #expect(WatchPolicy.ready(changes: ["/p/repo/a.jsonl": ago(9)], now: now).isEmpty)
    }

    @Test("a project with anything still being written is left alone entirely")
    func liveProjectBlocksItsSessions() {
        // `a` finished long ago, but `b` is live in the same repo: you are still
        // working there, so nothing in it is taken yet.
        let changes = ["/p/repo/a.jsonl": ago(60), "/p/repo/b.jsonl": ago(1)]
        #expect(WatchPolicy.ready(changes: changes, now: now).isEmpty)
    }

    @Test("a live session in one repo does not hold up another")
    func projectsAreIndependent() {
        let changes = ["/p/one/a.jsonl": ago(60), "/p/two/b.jsonl": ago(1)]
        #expect(WatchPolicy.ready(changes: changes, now: now) == ["/p/one/a.jsonl"])
    }

    @Test("the quiet period is the user's to change")
    func customQuiet() {
        let changes = ["/p/repo/a.jsonl": ago(3)]
        #expect(WatchPolicy.ready(changes: changes, now: now, quiet: 2 * 60) == ["/p/repo/a.jsonl"])
    }
}
