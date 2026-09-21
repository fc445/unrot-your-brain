//  SurfaceStoreTests.swift
//  UnrotKitTests
//
//  The rules the port is most likely to break, asserted directly.
//
//  Every test here is about a distinction that renders as the same empty box
//  if it is lost, which is what makes them worth writing: nothing on screen
//  would look wrong.

import Foundation
import Testing

@testable import UnrotKit

@Suite("What the client is allowed to decide")
struct SurfaceStoreTests {

    @Test("failed is derived from a request that did not return")
    @MainActor
    func failureIsDerivedNotDecoded() async {
        let store = SurfaceStore(client: UnrotClient(socketPath: "/tmp/nope-\(UUID().uuidString).sock"))
        await store.load()

        #expect(store.state == .failed)
        #expect(store.emptyState == .failed)
        #expect(store.failure != nil)
    }

    @Test("a clean surface is not the same shape as a failure")
    @MainActor
    func cleanIsNotFailure() throws {
        let store = SurfaceStore(client: UnrotClient(socketPath: "/tmp/unused.sock"))
        try store.accept(fixture(state: "clean"))

        #expect(store.state == .clean)
        #expect(store.emptyState == .clean)
        #expect(store.failure == nil)
    }

    @Test("sections keep their order, and the page ends on closed")
    @MainActor
    func sectionOrderIsTheShameSpiralGuard() {
        #expect(BucketSection.all.map(\.bucket) == [.open, .learning, .closed])
        #expect(BucketSection.all.last?.bucket == .closed)
    }

    @Test("a bucket the server adds later does not crash this build")
    @MainActor
    func unknownBucketDegrades() throws {
        let store = SurfaceStore(client: UnrotClient(socketPath: "/tmp/unused.sock"))
        try store.accept(fixture(state: "gaps", bucket: "deferred"))

        // Rendered nowhere, because no section claims it -- but decoded, and
        // the other concepts still appear.
        #expect(store.surface?.concepts.count == 1)
        #expect(store.populated.isEmpty)
    }

    @Test("an absent distribution stays absent rather than becoming three zeroes")
    func absentDistributionIsNotFlat() throws {
        let json = #"""
        {"explanation_id":"e1","raw_text":"x","prompt_text":"q","prompt_version":"c1",
         "submitted_at":"2026-01-01","level":null,"reasoning":null,"probabilities":null,
         "confidence":null,"grader_version":null}
        """#
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let explanation = try decoder.decode(Explanation.self, from: Data(json.utf8))

        #expect(explanation.probabilities == nil)
        #expect(explanation.level == nil)
    }

    // MARK: - Fixtures

    private func fixture(state: String, bucket: String = "open") -> Data {
        Data(#"""
        {"state":"\#(state)","headline":"H","detail":"D",
         "capture":{"sessions":3,"human_turns":9,"last_activity":null,
                    "sessions_analysed":3,"sessions_clean":3},
         "counts":{"open":0,"learning":0,"closed":0},"fixtures":0,
         "concepts":[{"concept_id":"c1","name":"backpressure","gap_type":"term",
                      "state":"gap","bucket":"\#(bucket)","aliases":[],
                      "encounter_count":1,"unjudged":1,"first_seen_at":null,
                      "last_seen_at":null,"latest_level":null,
                      "encounters":[],"explanations":[],"material":[]}]}
        """#.utf8)
    }
}

/// `load()` needs a core; these tests are about what the store does with a
/// payload rather than about fetching one.
extension SurfaceStore {
    @MainActor
    func accept(_ json: Data) throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        surface = try decoder.decode(Surface.self, from: json)
    }
}
