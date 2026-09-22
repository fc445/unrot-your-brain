//  UnrotClient.swift
//  UnrotKit
//
//  The typed API. A direct port of ui/src/api.ts, including the property that
//  file exists to hold: **every call either returns data or throws.** Nothing
//  here returns an empty result on error.
//
//  That is journey 3's hard requirement. "We looked and found nothing" and
//  "the core is down" both produce an empty screen unless the failure is
//  carried as its own thing all the way to the renderer, and the cheapest way
//  to lose it is a `try?` somewhere in the middle.

import Foundation

/// Anything that is not a successful JSON response.
public enum APIError: Error, Sendable {
    /// The core could not be reached. This is what `SurfaceState.failed` is
    /// derived from -- the server never sends that state, because a response
    /// saying "I failed" is a response that arrived.
    case unreachable(String)
    /// The core answered, and said no. `detail` is the server's own words,
    /// which are usually better than anything we would write over them.
    case refused(status: Int, detail: String)
    case undecodable(String)

    public var message: String {
        switch self {
        case .unreachable:
            return "Could not reach the unrot core."
        case .refused(_, let detail):
            return detail
        case .undecodable(let what):
            return "The core said something this build could not read: \(what)"
        }
    }

    /// Whether the failure was the transport rather than the answer.
    ///
    /// A refusal is the product working -- a 422 from the material gate is the
    /// provenance check doing its job -- and must stay on the card that asked
    /// for it. Only this replaces the page.
    public var isTransport: Bool {
        if case .unreachable = self { return true }
        return false
    }
}

public struct UnrotClient: Sendable {
    private let http: UnixSocketHTTP

    public init(socketPath: String, timeout: TimeInterval = 120) {
        self.http = UnixSocketHTTP(socketPath: socketPath, timeout: timeout)
    }

    public var socketPath: String { http.socketPath }

    // MARK: - Reads

    /// Cheap liveness. Given its own short timeout: the supervisor polls it,
    /// and a poll that waits two minutes is not a poll.
    public func health(timeout: TimeInterval = 5) async throws -> Health {
        let short = UnixSocketHTTP(socketPath: http.socketPath, timeout: timeout)
        return try decode(Health.self, await perform("GET", "/api/health", over: short))
    }

    /// Everything the first paint needs, in one request -- so the list and the
    /// status can never disagree about whether anything was found.
    public func surface() async throws -> Surface {
        try await get(Surface.self, "/api/surface")
    }

    public func moment(encounterId: String) async throws -> Moment {
        try await get(Moment.self, "/api/encounters/\(escape(encounterId))/moment")
    }

    public func check(conceptId: String) async throws -> Check {
        try await get(Check.self, "/api/concepts/\(escape(conceptId))/check")
    }

    // MARK: - Writes
    //
    // Every one of these goes through the same API the web surface uses, which
    // appends an event and recompiles. There is no second write path, and
    // nothing here decides what the write meant.

    public func judge(encounterId: String, _ verdict: Verdict) async throws -> JudgmentResult {
        try await post(JudgmentResult.self, "/api/encounters/\(escape(encounterId))/\(verdict.rawValue)")
    }

    /// Take a judgment back. A third event, not a delete: the log keeps the
    /// mis-key and the correction, and the fold reads whichever came last.
    public func retract(encounterId: String) async throws -> JudgmentResult {
        try await post(JudgmentResult.self, "/api/encounters/\(escape(encounterId))/retract")
    }

    /// A term met outside a session. Only the selection, and a title the user
    /// can switch off -- nothing about the document it came from.
    public func submit(text: String, ownWords: String? = nil, seenIn: String? = nil) async throws -> Submitted {
        var payload: [String: String] = ["text": text]
        if let ownWords, !ownWords.isEmpty { payload["paraphrase"] = ownWords }
        if let seenIn, !seenIn.isEmpty { payload["seen_in"] = seenIn }
        let body = try JSONSerialization.data(withJSONObject: payload)
        return try await post(Submitted.self, "/api/submissions", body: body)
    }

    /// "No -- this is new": argue with the resolver and give the encounter a
    /// concept of its own.
    public func splitOut(judgmentEventId: String, encounterId: String, reasoning: String) async throws -> Corrected {
        let body = try JSONSerialization.data(withJSONObject: [
            "reasoning": reasoning, "split_encounter": encounterId,
        ])
        return try await post(Corrected.self, "/api/judgments/\(escape(judgmentEventId))/correct", body: body)
    }

    // MARK: - The watcher

    public func queue() async throws -> AnalysisQueue {
        try await get(AnalysisQueue.self, "/api/queue")
    }

    /// Copy transcripts into the raw store. No model, no cost.
    public func capture(paths: [String]) async throws -> CaptureResult {
        let body = try JSONSerialization.data(withJSONObject: ["paths": paths])
        return try await post(CaptureResult.self, "/api/capture", body: body)
    }

    /// Analyse one session. The call that costs, so nothing makes it but a
    /// click or a setting the user switched on. Given a long timeout: a long
    /// session is several model calls, and giving up on one that is working
    /// would only make the retry pay again.
    public func analyse(sessionId: String) async throws -> Analysed {
        let body = try JSONSerialization.data(withJSONObject: ["session_id": sessionId])
        let patient = UnixSocketHTTP(socketPath: http.socketPath, timeout: 900)
        return try decode(Analysed.self, await perform("POST", "/api/analyse", body: body, over: patient))
    }

    // MARK: - Settings and regeneration

    public func config() async throws -> CoreConfig {
        try await get(CoreConfig.self, "/api/config")
    }

    public func regenPlan() async throws -> RegenPlan {
        try await get(RegenPlan.self, "/api/regen/plan")
    }

    /// Re-examine one session under the current detector. Costs model calls.
    public func regen(sessionId: String) async throws -> RegenPass {
        let body = try JSONSerialization.data(withJSONObject: ["session_id": sessionId])
        let patient = UnixSocketHTTP(socketPath: http.socketPath, timeout: 900)
        return try decode(RegenPass.self, await perform("POST", "/api/regen", body: body, over: patient))
    }

    public func explain(conceptId: String, text: String) async throws -> Graded {
        let body = try JSONSerialization.data(withJSONObject: ["text": text])
        return try await post(Graded.self, "/api/concepts/\(escape(conceptId))/explanation", body: body)
    }

    public func makeMaterial(conceptId: String, format: MaterialFormat) async throws -> Made {
        try await post(
            Made.self,
            "/api/concepts/\(escape(conceptId))/material?format=\(escape(format.rawValue))"
        )
    }

    // MARK: - Plumbing

    private func get<T: Decodable>(_ type: T.Type, _ path: String) async throws -> T {
        try decode(type, await perform("GET", path))
    }

    private func post<T: Decodable>(_ type: T.Type, _ path: String, body: Data? = nil) async throws -> T {
        try decode(type, await perform("POST", path, body: body))
    }

    /// The transport, with its errors translated once.
    ///
    /// `TransportError` never escapes this type. Everything above it deals in
    /// `APIError`, where `unreachable` is the case `SurfaceState.failed` is
    /// built from -- so the translation happening in exactly one place is what
    /// keeps that derivation honest.
    private func perform(
        _ method: String,
        _ path: String,
        body: Data? = nil,
        over transport: UnixSocketHTTP? = nil
    ) async throws -> HTTPResponse {
        do {
            return try await (transport ?? http).send(method: method, path: path, body: body)
        } catch let error as TransportError {
            throw APIError.unreachable(String(describing: error))
        }
    }

    private func decode<T: Decodable>(_ type: T.Type, _ response: HTTPResponse) throws -> T {
        guard response.isSuccess else {
            throw APIError.refused(status: response.status, detail: Self.detail(from: response))
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do {
            return try decoder.decode(T.self, from: response.body)
        } catch {
            throw APIError.undecodable("\(type) -- \(error)")
        }
    }

    /// FastAPI puts the message in `detail`. A non-JSON error body is still
    /// worth showing verbatim rather than replacing with something vaguer.
    private static func detail(from response: HTTPResponse) -> String {
        if let object = try? JSONSerialization.jsonObject(with: response.body) as? [String: Any],
           let detail = object["detail"] as? String, !detail.isEmpty {
            return detail
        }
        let text = String(data: response.body, encoding: .utf8) ?? ""
        return text.isEmpty ? "HTTP \(response.status)" : text
    }

    private func escape(_ component: String) -> String {
        component.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? component
    }
}
