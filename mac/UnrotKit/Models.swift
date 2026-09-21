//  Models.swift
//  UnrotKit
//
//  Mirrors src/unrot/api/schemas.py. Hand-written rather than generated, for
//  the reason ui/src/types.ts already gives: the API is small, and a generator
//  is a build step to maintain for a contract that fits on one screen.
//
//  Nothing in this file computes anything. Every field arrives decided.

import Foundation

// MARK: - Open enumerations
//
// These are structs wrapping a String rather than Swift enums, deliberately.
//
// Two reasons, and the second is the important one. A `case unknown(String)`
// enum survives a value the server adds later; so does this, with less
// ceremony. But a closed enum also invites `switch` exhaustiveness, and an
// exhaustive switch over buckets in Swift is the beginning of Swift deciding
// what a bucket means -- which is the one thing this port is not allowed to do.
// A type you cannot switch exhaustively over is a type you tend to render
// rather than reason about.

/// Which section a concept renders in. Decided by `api/read.py`, never here.
public struct Bucket: RawRepresentable, Codable, Hashable, Sendable {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }

    public static let open = Bucket(rawValue: "open")
    public static let learning = Bucket(rawValue: "learning")
    public static let closed = Bucket(rawValue: "closed")
}

/// What state the whole surface is in.
///
/// `failed` is the one value the server never sends, because a response saying
/// "I failed" is a response that arrived. The client derives it from a request
/// that did not return -- see `UnrotClient`. Journey 3 turns on the difference
/// between this and `clean`.
public struct SurfaceState: RawRepresentable, Codable, Hashable, Sendable {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }

    public static let gaps = SurfaceState(rawValue: "gaps")
    public static let clean = SurfaceState(rawValue: "clean")
    public static let coldStart = SurfaceState(rawValue: "cold_start")
    public static let notCaptured = SurfaceState(rawValue: "not_captured")
    public static let notAnalysed = SurfaceState(rawValue: "not_analysed")

    /// Never decoded. Only ever constructed by the client from a failed request.
    public static let failed = SurfaceState(rawValue: "failed")
}

/// SOLO, collapsed to three. The only boundary carrying weight is
/// `listed -> causal`: where "I can recite what the model told me" separates
/// from "I understood it".
public struct SoloLevel: RawRepresentable, Codable, Hashable, Sendable {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }

    public static let isolated = SoloLevel(rawValue: "isolated")
    public static let listed = SoloLevel(rawValue: "listed")
    public static let causal = SoloLevel(rawValue: "causal")

    /// Display order, and the order the distribution bars are drawn in.
    public static let ordered: [SoloLevel] = [.isolated, .listed, .causal]
}

/// Both are shipped formats. `sourcesOnly` is not a degraded one -- it
/// generates nothing, and so can invent nothing.
public struct MaterialFormat: RawRepresentable, Codable, Hashable, Sendable {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }

    public static let textual = MaterialFormat(rawValue: "textual_with_sources")
    public static let sourcesOnly = MaterialFormat(rawValue: "sources_only")
}

/// What the user said about a flag. Ground truth; never replayed by regeneration.
public enum Verdict: String, Codable, Hashable, Sendable {
    /// "I genuinely did not know this." Deliberately does *not* close the gap --
    /// it is the beginning of learning it.
    case confirm
    /// "The flag was wrong." Signal for tuning the detector, not a deletion.
    case dismiss
}

// MARK: - The wire types

public struct Encounter: Codable, Hashable, Sendable, Identifiable {
    public let encounterId: String
    /// `transcript` or `manual`. Provenance, not ranking: a gap you typed in
    /// yourself is as real as one we found, and must render identically.
    public let source: String
    public let paraphrase: String?
    public let judgment: String?
    public let judgedAt: String?
    public let occurredAt: String
    public let detectorVersion: String?
    public let sessionId: String?
    public let lineStart: Int?
    public let lineEnd: Int?
    /// Whether the raw transcript is still on this machine. False on the
    /// portable layer by construction, so a card must read fine without it.
    public let resolvable: Bool

    public var id: String { encounterId }
}

public struct Explanation: Codable, Hashable, Sendable, Identifiable {
    public let explanationId: String
    public let rawText: String
    /// The question this answer was given to, stored verbatim. Shown back with
    /// the answer because the same words mean different things under different
    /// questions, and the wording is expected to change over time.
    public let promptText: String
    public let promptVersion: String
    public let submittedAt: String
    /// Nil while ungraded, which is a normal state: the answer is stored before
    /// grading is attempted, so a failed grader never costs what was written.
    public let level: SoloLevel?
    public let reasoning: String?
    /// Only a classifier supplies these. **Absent means "not measured", not
    /// "flat"** -- render nothing rather than three zeroes.
    public let probabilities: [String: Double]?
    public let confidence: Double?
    public let graderVersion: String?

    public var id: String { explanationId }
}

/// `MaterialOut` on the wire. Renamed here because SwiftUI already has a
/// `Material` -- the blur -- and a view file importing both frameworks cannot
/// say which it means.
public struct LearningMaterial: Codable, Hashable, Sendable, Identifiable {
    public let materialId: String
    public let format: MaterialFormat
    public let body: String?
    public let sources: [Source]
    public let generatedAt: String
    public let deliveredAt: String?

    public var id: String { materialId }

    /// Loosely typed on the wire (`list[dict]`), so it is decoded permissively:
    /// a source that arrives with fields this build has never heard of is still
    /// a source, and dropping it would quietly make material look ungrounded.
    public struct Source: Codable, Hashable, Sendable {
        public let title: String?
        public let url: String?
        public let note: String?
    }
}

public struct Concept: Codable, Hashable, Sendable, Identifiable {
    public let conceptId: String
    public let name: String
    public let gapType: String
    public let state: String
    public let bucket: Bucket
    public let aliases: [String]
    public let encounterCount: Int
    public let unjudged: Int
    public let firstSeenAt: String?
    public let lastSeenAt: String?
    public let latestLevel: SoloLevel?
    public let encounters: [Encounter]
    public let explanations: [Explanation]
    public let material: [LearningMaterial]

    public var id: String { conceptId }
}

public struct CaptureStats: Codable, Hashable, Sendable {
    public let sessions: Int
    public let humanTurns: Int
    public let lastActivity: String?
    /// The number that makes silence mean something. `session_analysed` is
    /// appended even when the detector found nothing, so "we looked and found
    /// nothing" is distinguishable from "nothing ever looked".
    public let sessionsAnalysed: Int
    public let sessionsClean: Int
}

public struct Surface: Codable, Hashable, Sendable {
    public let state: SurfaceState
    /// Written by the server. Never composed here, for any state.
    public let headline: String
    public let detail: String
    public let capture: CaptureStats
    public let counts: [String: Int]
    /// Non-zero means some of what is on screen is fake. Seeded data must never
    /// read as a finding about the user, so the surface says so.
    public let fixtures: Int
    public let concepts: [Concept]

    public func count(_ bucket: Bucket) -> Int { counts[bucket.rawValue] ?? 0 }
}

public struct JudgmentResult: Codable, Hashable, Sendable {
    public let encounterId: String
    /// The concept after recompiling, so the client re-renders from the store's
    /// answer rather than from its own guess at what the judgment did.
    public let concept: Concept?
    public let surface: SurfaceState
    public let counts: [String: Int]
}

public struct MomentTurn: Codable, Hashable, Sendable, Identifiable {
    public let lineNo: Int
    public let role: String
    public let text: String
    public let isMeta: Bool
    public let isSidechain: Bool

    public var id: Int { lineNo }
}

public struct Moment: Codable, Hashable, Sendable {
    public let encounterId: String
    public let resolvable: Bool
    /// Why it cannot be replayed, when it cannot. Three distinct reasons, all
    /// of them honest answers rather than errors.
    public let reason: String?
    public let sessionId: String?
    public let lineStart: Int?
    public let lineEnd: Int?
    public let turns: [MomentTurn]
}

public struct Check: Codable, Hashable, Sendable {
    public let conceptId: String
    public let name: String
    /// Displayed exactly as sent. If the words on screen drift from the words
    /// stored against the answer, re-grading silently stops being valid.
    public let promptText: String
    public let promptVersion: String
}

public struct Graded: Codable, Hashable, Sendable {
    public let explanation: Explanation
    /// A `causal` grade compiles the concept to `known`, so this can arrive in
    /// a different bucket than it left.
    public let concept: Concept?
    public let counts: [String: Int]
    /// False when the answer was stored but no grader was available. It means
    /// the check did not run -- never that the user failed it.
    public let graded: Bool
}

public struct Made: Codable, Hashable, Sendable {
    public let material: LearningMaterial
    public let concept: Concept?
    public let counts: [String: Int]
}

public struct Health: Codable, Hashable, Sendable {
    public let ok: Bool
    public let version: String
    public let compiledSchema: Int
    /// False means the moment view cannot resolve anything on this machine.
    /// A shipped state, not a fault.
    public let rawOpen: Bool
    public let storePath: String
}
