//  QuickAcceptTests.swift
//  UnrotKitTests
//
//  Two keys and an undo. Played against a stub that keeps one encounter's
//  judgment as state, so what is asserted is what the core was actually sent.

import Foundation
import Testing

@testable import UnrotKit

/// One waiting gap, and a record of every request made about it.
final class OneGapCore: @unchecked Sendable {
    private let lock = NSLock()
    private var judgment: String?
    private(set) var requests: [String] = []
    var failJudgments = false

    func handle(_ method: String, _ path: String) -> Data {
        lock.lock(); defer { lock.unlock() }
        requests.append("\(method) \(path)")
        switch (method, path) {
        case ("POST", let p) where p.hasSuffix("/confirm") || p.hasSuffix("/dismiss"):
            if failJudgments { return Self.reply(500, #"{"detail":"boom"}"#) }
            judgment = p.hasSuffix("/confirm") ? "confirmed" : "dismissed"
            return Self.reply(200, judgmentBody())
        case ("POST", let p) where p.hasSuffix("/retract"):
            judgment = nil
            return Self.reply(200, judgmentBody())
        case ("GET", "/api/surface"):
            return Self.reply(200, surfaceBody())
        default:
            return Self.reply(404, #"{"detail":"not in this stub"}"#)
        }
    }

    private var bucket: String {
        switch judgment { case "confirmed": "learning"; case "dismissed": "closed"; default: "open" }
    }

    private func concept() -> String {
        let judged = judgment.map { "\"\($0)\"" } ?? "null"
        return #"""
        {"concept_id":"c1","name":"backpressure","gap_type":"term","state":"gap",
         "bucket":"\#(bucket)","aliases":[],"encounter_count":1,"unjudged":\#(judgment == nil ? 1 : 0),
         "first_seen_at":null,"last_seen_at":null,"latest_level":null,
         "encounters":[{"encounter_id":"e1","source":"manual","paraphrase":"p","judgment":\#(judged),
                        "judged_at":null,"occurred_at":"2026-09-01","detector_version":null,
                        "session_id":null,"line_start":null,"line_end":null,"resolvable":false}],
         "explanations":[],"material":[]}
        """#
    }

    private func counts() -> String {
        #"{"open":\#(bucket == "open" ? 1 : 0),"learning":\#(bucket == "learning" ? 1 : 0),"closed":\#(bucket == "closed" ? 1 : 0)}"#
    }

    private func judgmentBody() -> String {
        #"{"encounter_id":"e1","concept":\#(concept()),"surface":"gaps","counts":\#(counts())}"#
    }

    private func surfaceBody() -> String {
        #"""
        {"state":"\#(bucket == "open" ? "gaps" : "clean")","headline":"H","detail":"D",
         "capture":{"sessions":3,"human_turns":9,"last_activity":null,"sessions_analysed":3,"sessions_clean":2},
         "counts":\#(counts()),"fixtures":0,"concepts":[\#(concept())]}
        """#
    }

    static func reply(_ status: Int, _ body: String) -> Data {
        Data("HTTP/1.1 \(status) X\r\nContent-Type: application/json\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)".utf8)
    }
}

@Suite("Quick accept")
@MainActor
struct QuickAcceptTests {

    private func setUp(window: Duration = .seconds(8)) async throws -> (OneGapCore, StubServer, SurfaceStore, QuickAccept) {
        let core = OneGapCore()
        let server = try StubServer { core.handle($0, $1) }
        let store = SurfaceStore(client: UnrotClient(socketPath: server.path, timeout: 5))
        await store.load()
        return (core, server, store, QuickAccept(store: store, undoWindow: window))
    }

    @Test("one key answers the top gap and offers an undo")
    func answerOffersUndo() async throws {
        let (core, server, store, quick) = try await setUp()
        _ = server

        let answer = await quick.answerNext(.confirm)

        #expect(answer?.encounterId == "e1")
        #expect(quick.undoable == answer)
        #expect(store.waitingCount == 0)
        #expect(core.requests.contains("POST /api/encounters/e1/confirm"))
    }

    @Test("the undo is a retraction sent to the core, not a local rollback")
    func undoRetracts() async throws {
        let (core, server, store, quick) = try await setUp()
        _ = server
        await quick.answerNext(.dismiss)

        let undone = await quick.undo()

        #expect(undone)
        #expect(quick.undoable == nil)
        #expect(core.requests.contains("POST /api/encounters/e1/retract"))
        // The store re-read rather than patched itself, so the gap is back
        // because the core says it is.
        #expect(store.waitingCount == 1)
    }

    @Test("the undo window closes")
    func undoExpires() async throws {
        let (_, server, _, quick) = try await setUp(window: .milliseconds(80))
        _ = server
        await quick.answerNext(.confirm)
        #expect(quick.undoable != nil)

        try await Task.sleep(for: .milliseconds(300))

        #expect(quick.undoable == nil)
        #expect(await quick.undo() == false)
    }

    @Test("an answer that did not save offers no undo")
    func failedAnswerOffersNothing() async throws {
        let (core, server, _, quick) = try await setUp()
        _ = server
        core.failJudgments = true

        let answer = await quick.answerNext(.confirm)

        #expect(answer == nil)
        #expect(quick.undoable == nil)
    }

    @Test("quick accept never reaches the comprehension check")
    func neverTouchesTheCheck() async throws {
        let (core, server, _, quick) = try await setUp()
        _ = server

        await quick.answerNext(.confirm)
        await quick.undo()
        await quick.answerNext(.dismiss)

        // The check asks for causation and is meant to be slow. A one-key path
        // to it would manufacture the fluent answers its rubric exists to catch.
        #expect(!core.requests.contains { $0.contains("/check") || $0.contains("/explanation") })
    }
}
