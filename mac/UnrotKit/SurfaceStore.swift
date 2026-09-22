//  SurfaceStore.swift
//  UnrotKit
//
//  The view model, and a deliberate mirror of ui/src/App.tsx's state.
//
//  Keeping the two shapes aligned is not tidiness. When one surface
//  misbehaves and the other does not, the difference between them is the
//  entire diagnosis, and that is only cheap while they are comparable.
//
//  `Observation`, not SwiftUI: this target has to compile for iOS, and later
//  for anything else, without carrying a view framework.

import Foundation
import Observation

/// Section headings, order and copy. Verbatim from `App.tsx`.
///
/// This is content, not policy -- which bucket a concept is *in* is decided by
/// `api/read.py` and arrives already decided. What is decided here is only
/// that the page is read in this order, and that matters:
///
/// > The order matters: what needs you, then what you are working on, then
/// > what is behind you. Ending on "closed" is the whole shame-spiral guard --
/// > the last thing on the page is progress, not deficit.
/// Named for the same reason `LearningMaterial` is: SwiftUI's own `Section`
/// would otherwise be ambiguous in every view that renders one of these.
public struct BucketSection: Sendable, Hashable, Identifiable {
    public let bucket: Bucket
    public let title: String
    public let note: String

    public var id: String { bucket.rawValue }

    public static let all: [BucketSection] = [
        BucketSection(
            bucket: .open,
            title: "Waiting on you",
            note: "Leaned on in a session, and waved through"
        ),
        BucketSection(
            bucket: .learning,
            title: "To learn",
            note: "You said you didn't know these"
        ),
        BucketSection(
            bucket: .closed,
            title: "Closed",
            note: "Either you knew it, or you explained it"
        ),
    ]
}

/// Per-concept material state. A refusal lives here rather than on the page,
/// because a refusal is the provenance gate working and not a broken core.
public struct MaterialRequest: Sendable, Hashable {
    public var busy: Bool = false
    public var error: String?
    /// The refusal's HTTP status: 422 is "not grounded", 409 "would recurse".
    /// Two different reasons, and the card says which.
    public var status: Int?
}

@MainActor
@Observable
public final class SurfaceStore {
    /// `internal(set)` rather than `private(set)`: tests set a decoded surface
    /// directly to exercise what the store does with a payload, without a core.
    /// Nothing outside UnrotKit can write it.
    public internal(set) var surface: Surface?
    /// Set only by a transport failure. A refusal never lands here.
    public private(set) var failure: String?
    public private(set) var busy: Set<String> = []
    /// Held above the card, because a `causal` grade moves the concept to a
    /// different section and destroys the card it was shown on.
    public var justGraded: Graded?
    public private(set) var material: [String: MaterialRequest] = [:]
    public private(set) var hasLoadedOnce = false

    private let client: UnrotClient

    public init(client: UnrotClient) {
        self.client = client
    }

    /// The one piece of state this client derives rather than receives.
    ///
    /// `failed` is not in any response by construction, so it is assembled here
    /// from a request that did not return. Everything else is read off the
    /// surface exactly as sent.
    public var state: SurfaceState {
        if failure != nil { return .failed }
        return surface?.state ?? .gaps
    }

    /// Sections with something in them, in the fixed order.
    public var populated: [BucketSection] {
        guard let surface else { return [] }
        return BucketSection.all.filter { surface.count($0.bucket) > 0 }
    }

    /// Non-nil when the page is a single full-width answer rather than a list.
    ///
    /// An empty state carries its own headline and its own next step, so the
    /// page-level heading would only repeat it back.
    public var emptyState: SurfaceState? {
        if failure != nil { return .failed }
        guard let surface else { return nil }
        return surface.state != .gaps && populated.isEmpty ? surface.state : nil
    }

    public func concepts(in bucket: Bucket) -> [Concept] {
        surface?.concepts.filter { $0.bucket == bucket } ?? []
    }

    public func isBusy(_ concept: Concept) -> Bool {
        concept.encounters.contains { busy.contains($0.encounterId) }
    }

    public func materialState(_ conceptId: String) -> MaterialRequest {
        material[conceptId] ?? MaterialRequest()
    }

    // MARK: - Loading

    public func load() async {
        do {
            surface = try await client.surface()
            failure = nil
        } catch let error as APIError {
            // Journey 3: a failure must never render as an empty list. The old
            // surface is deliberately kept -- replacing it with nil would turn a
            // momentary blip into a blank page and lose what was on screen.
            failure = error.message
        } catch {
            failure = "Something broke while loading your gaps."
        }
        hasLoadedOnce = true
    }

    // MARK: - Writing

    public func judge(_ encounterId: String, _ verdict: Verdict) async {
        busy.insert(encounterId)
        defer { busy.remove(encounterId) }
        do {
            _ = try await client.judge(encounterId: encounterId, verdict)
            // Re-read rather than patching local state. The judgment recompiles
            // the whole graph and a confirm can move a concept between sections;
            // asking the store what happened is cheaper than predicting it
            // correctly here, and cannot drift from what the store actually did.
            await load()
        } catch let error as APIError {
            failure = error.isTransport ? error.message : "That judgment did not save: \(error.message)"
        } catch {
            failure = "That judgment did not save."
        }
    }

    /// Undo a judgment. Returns false if it did not land.
    @discardableResult
    public func retract(_ encounterId: String) async -> Bool {
        busy.insert(encounterId)
        defer { busy.remove(encounterId) }
        do {
            _ = try await client.retract(encounterId: encounterId)
            await load()
            return true
        } catch let error as APIError {
            failure = error.isTransport ? error.message : "That undo did not save: \(error.message)"
            return false
        } catch {
            return false
        }
    }

    /// The gap quick accept would answer next: the first unanswered encounter
    /// in the first waiting concept, in the order the server sent them.
    ///
    /// Picks nothing and ranks nothing. `read.py` decides what is waiting and
    /// in what order; this takes the top of what it was given.
    public var nextWaiting: (concept: Concept, encounter: Encounter)? {
        for concept in concepts(in: .open) {
            if let encounter = concept.encounters.first(where: { $0.judgment == nil }) {
                return (concept, encounter)
            }
        }
        return nil
    }

    public var waitingCount: Int { surface?.count(.open) ?? 0 }

    public func submitExplanation(conceptId: String, text: String) async -> Graded? {
        do {
            let result = try await client.explain(conceptId: conceptId, text: text)
            justGraded = result
            await load()
            return result
        } catch let error as APIError {
            failure = error.message
            return nil
        } catch {
            failure = "That answer did not save."
            return nil
        }
    }

    public func makeMaterial(conceptId: String, format: MaterialFormat) async {
        material[conceptId] = MaterialRequest(busy: true, error: nil)
        do {
            _ = try await client.makeMaterial(conceptId: conceptId, format: format)
            material[conceptId] = MaterialRequest(busy: false, error: nil)
            await load()
        } catch let error as APIError {
            // A refusal -- 409 WouldRecurse, 422 NotGrounded -- is the gate
            // working. It stays on the card with the server's own wording
            // rather than replacing the page with an error state.
            var status: Int?
            if case .refused(let code, _) = error { status = code }
            material[conceptId] = MaterialRequest(busy: false, error: error.message, status: status)
            if error.isTransport { failure = error.message }
        } catch {
            material[conceptId] = MaterialRequest(busy: false, error: "Could not make anything for that.")
        }
    }

    public func submit(text: String, ownWords: String?, seenIn: String?) async throws -> Submitted {
        let result = try await client.submit(text: text, ownWords: ownWords, seenIn: seenIn)
        await load()
        return result
    }

    public func splitOut(_ submitted: Submitted, reasoning: String) async throws -> Corrected {
        let result = try await client.splitOut(
            judgmentEventId: submitted.judgmentEventId,
            encounterId: submitted.encounterId,
            reasoning: reasoning
        )
        await load()
        return result
    }

    public func check(conceptId: String) async throws -> Check {
        try await client.check(conceptId: conceptId)
    }

    public func moment(encounterId: String) async throws -> Moment {
        try await client.moment(encounterId: encounterId)
    }
}
