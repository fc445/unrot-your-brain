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
    /// The repo the session ran in, when the raw layer is on this machine.
    public let repo: String?

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

    /// `material/sources.py`'s `Source`: `kind` is `code`, `transcript` or
    /// `web`, and `ref` is a path, a session pointer or a URL accordingly.
    /// Decoded permissively -- a source with fields this build has never heard
    /// of is still a source, and dropping it would make material look ungrounded.
    public struct Source: Codable, Hashable, Sendable {
        public let kind: String?
        public let ref: String?
        public let title: String?
        public let excerpt: String?
        public let verified: Bool?
        public let note: String?

        /// A link only for the web: a code path or a session pointer is not
        /// somewhere a browser can go.
        public var url: URL? {
            guard kind == "web", let ref, ref.hasPrefix("http") else { return nil }
            return URL(string: ref)
        }

        /// "ietf.org", "your machine", "this session" -- where it lives.
        public var whereFrom: String? {
            switch kind {
            case "web": return url?.host()?.replacingOccurrences(of: "www.", with: "")
            case "code": return ref.map { "your machine · \($0)" } ?? "your machine"
            case "transcript": return "the session this came up in"
            default: return ref
            }
        }
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
    /// Sessions with something a person typed, the only ones the detector can
    /// examine, and how many of those are still waiting. `sessions` counts
    /// all-tool sessions too, so it is the wrong denominator. Optional so an
    /// older core still decodes.
    public let sessionsAnalysable: Int?
    public let sessionsWaiting: Int?
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

public struct Submitted: Codable, Hashable, Sendable {
    public let encounterId: String
    public let conceptId: String
    public let canonicalName: String
    /// `new`, `existing` or `alias`. Only the last two are arguable.
    public let decision: String
    public let reasoning: String
    /// The address of the resolver's judgment -- what a correction names.
    public let judgmentEventId: String
    public let decidedWithoutModel: Bool
    /// Set only when a model should have answered and could not.
    public let modelUnavailable: String?
    public let concept: Concept?
    public let counts: [String: Int]

    /// Whether there is a judgment here worth offering an argument with.
    /// A string match on the same name, or a concept created new, is not one.
    public var isArguable: Bool {
        (decision == "existing" || decision == "alias") && !decidedWithoutModel
    }
}

public struct Corrected: Codable, Hashable, Sendable {
    public let correctionEventId: String
    public let concept: Concept?
    public let counts: [String: Int]
}

/// A captured session waiting to be analysed. Derived by the core from what
/// capture holds and what the log says was examined -- nothing stores it.
public struct PendingSession: Codable, Hashable, Sendable, Identifiable {
    public let sessionId: String
    public let lastActivity: String?
    public let humanTurns: Int
    /// `never` -- captured, not yet examined; `grown` -- examined, then continued.
    public let reason: String
    public let analysedAt: String?
    public let cwd: String?

    public var id: String { sessionId }

    /// The repo, by its last path component: enough to recognise, no more.
    public var repo: String? {
        cwd.map { ($0 as NSString).lastPathComponent }
    }
}

public struct AnalysisQueue: Codable, Hashable, Sendable {
    public let pending: [PendingSession]
    /// Whether a model is configured. Capture never needs one; analysis does.
    public let canAnalyse: Bool
    public let analysing: [String]
    /// Roughly what analysing everything pending would cost. Optional so an
    /// older core, which does not send it, still decodes.
    public let estimate: SpendEstimate?
    /// The last seven days' spend, for the queue bar and the tray.
    public let spentThisWeek: SpendTotal?
}

// MARK: - Spend (PR-31)
//
// Every number and every sentence here is the core's. `text` in particular is
// decided there -- "$0.14", "local, no cost", "$0.14 + 2 unpriced" -- so the
// app, the web page and `store metrics` cannot word the same total differently,
// and a local model can never be drawn as "$0.00".

/// A sum of model calls.
public struct SpendTotal: Codable, Hashable, Sendable {
    public let calls: Int
    public let failed: Int
    /// USD, over the calls that reported a price.
    public let cost: Double
    public let priced: Int
    public let local: Int
    /// Hosted calls that reported no price: unknown, not free.
    public let unpriced: Int
    public let promptTokens: Int
    public let completionTokens: Int
    public let reasoningTokens: Int
    public let localOnly: Bool
    public let text: String
}

/// What the money went on: finding gaps, filing, grading, material.
public struct SpendPart: Codable, Hashable, Sendable, Identifiable {
    public let purpose: String
    public let label: String
    public let total: SpendTotal

    public var id: String { purpose }
}

public struct SpendDay: Codable, Hashable, Sendable, Identifiable {
    /// A UTC day, YYYY-MM-DD.
    public let day: String
    public let total: SpendTotal

    public var id: String { day }
}

/// What examining one session has cost with one model. The number for
/// choosing a model.
public struct SpendPerSession: Codable, Hashable, Sendable, Identifiable {
    public let model: String
    public let sessions: Int
    public let cost: Double
    public let local: Bool
    public let average: Double?
    public let text: String

    public var id: String { model }
}

/// Roughly what a bulk run would cost, from recent sessions on the same model.
public struct SpendEstimate: Codable, Hashable, Sendable {
    public let model: String
    public let sessions: Int
    /// How many recent sessions the average came from. Zero: no estimate.
    public let basedOn: Int
    public let perSession: Double?
    public let local: Bool
    public let cost: Double?
    /// "about $0.12", "local, no cost", or nil when there is nothing to go on.
    public let text: String?
}

public struct Spend: Codable, Hashable, Sendable {
    /// The model in effect now.
    public let model: String
    public let local: Bool
    public let week: SpendTotal
    public let weekByPurpose: [SpendPart]
    public let allTime: SpendTotal
    public let allTimeByPurpose: [SpendPart]
    public let byDay: [SpendDay]
    public let perSession: [SpendPerSession]
    /// Set when asked `since:` -- the running total of a batch.
    public let window: SpendTotal?
}

public struct CaptureResult: Codable, Hashable, Sendable {
    public struct Captured: Codable, Hashable, Sendable {
        public let sessionId: String
        public let status: String
        public let turnsAdded: Int
    }
    public let captured: [Captured]
    public let pending: Int
}

public struct Analysed: Codable, Hashable, Sendable {
    public let sessionId: String
    public let clean: Bool
    public let filed: [String]
    public let windowsExamined: Int
    public let detectorVersion: String
    public let counts: [String: Int]
}

/// The model configuration the core resolved -- which is the only honest
/// answer to "what will be used", since a real environment variable beats the
/// app's settings.
public struct CoreConfig: Codable, Hashable, Sendable {
    public let model: String
    public let baseUrl: String
    public let keySet: Bool
    public let local: Bool
    /// `classifier` or `keyword`.
    public let grader: String
    public let detectorVersion: String
    /// Nil when no effort is sent: a local server, or the model left to decide.
    /// Optional so an older core still decodes.
    public let reasoningEffort: String?
    /// Empty when the endpoint is local.
    public let leavesThisMac: [String]
}

public struct RegenPlan: Codable, Hashable, Sendable {
    public let detectorVersion: String
    public let captured: Int
    public let alreadyDone: Int
    public let toRun: [String]
    /// Encounters carrying a user judgment. None of them will be touched.
    public let protected: Int
    public let canRun: Bool
}

public struct RegenPass: Codable, Hashable, Sendable {
    public let sessionId: String
    public let protected: Int
    public let removed: Int
    public let recorded: Int
    public let skipped: Bool
    public let detectorVersion: String
}

// MARK: - Export (PR-32)

public struct ExportFile: Codable, Hashable, Sendable {
    public let name: String
    public let rows: Int
}

/// What "Export for analysis" would write, shown before anything is.
public struct ExportPreview: Codable, Hashable, Sendable {
    public let includeText: Bool
    /// Suggested name for the `.zip`; it unpacks to a folder of the same stem.
    public let filename: String
    public let files: [ExportFile]
    public let fixtureEvents: Int
    public let firstEventAt: String?
    public let lastEventAt: String?
    /// The core's own wording, so the app cannot describe the bundle
    /// differently from what the code puts in it.
    public let included: [String]
    /// Empty when text is included.
    public let withheld: [String]
    public let never: [String]
}
