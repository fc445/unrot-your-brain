//  QuickAccept.swift
//  UnrotKit
//
//  Two keys and an undo, identical wherever they appear -- the popover, a
//  notification, the window, the triage panel.
//
//  An undo rather than a confirmation step, deliberately. A dialog taxes every
//  correct answer to guard against the rare wrong one; an undo costs nothing
//  when you were right. And because judgments are appended, the undo is a
//  correcting event rather than a deletion, so the mis-key stays legible.
//
//  **The hard limit:** this records `confirm` and `dismiss` and nothing else.
//  It has no route to the comprehension check and must never grow one. The
//  check asks for causation on purpose and is meant to be slow; a one-key path
//  to it would manufacture exactly the fluent, unconsidered answers the rubric
//  exists to catch.

import Foundation
import Observation

@MainActor
@Observable
public final class QuickAccept {

    public struct Answer: Equatable, Sendable {
        public let encounterId: String
        public let conceptName: String
        public let verdict: Verdict

        public var sentence: String {
            verdict == .confirm
                ? "Filed \(conceptName) under “to learn”."
                : "Marked \(conceptName) as known."
        }
    }

    /// The answer the undo strip is showing. Nil once its eight seconds are up:
    /// the strip is a reminder that an undo exists, not the undo itself.
    public private(set) var undoable: Answer?

    /// What Edit › Undo takes back: the last answer, until the next answer or
    /// an undo replaces it. It does not expire with the strip -- nothing about
    /// an answer changes at eight seconds, since it was saved when given, and a
    /// timer is no way to decide how long someone may take to notice a mis-key.
    public private(set) var lastAnswer: Answer?

    public let undoWindow: Duration
    private let store: SurfaceStore
    private var expiry: Task<Void, Never>?

    public init(store: SurfaceStore, undoWindow: Duration = .seconds(8)) {
        self.store = store
        self.undoWindow = undoWindow
    }

    /// Answer the gap on top of the pile.
    @discardableResult
    public func answerNext(_ verdict: Verdict) async -> Answer? {
        guard let (concept, encounter) = store.nextWaiting else { return nil }
        return await answer(concept: concept, encounter: encounter, verdict)
    }

    @discardableResult
    public func answer(concept: Concept, encounter: Encounter, _ verdict: Verdict) async -> Answer? {
        await store.judge(encounter.encounterId, verdict)
        // `judge` reports failure through the store rather than by throwing, so
        // read back whether it landed instead of assuming it did. Offering an
        // undo for an answer that never saved would be a small lie with a
        // confusing consequence: undoing it would 409.
        let landed = store.surface?.concepts
            .flatMap(\.encounters)
            .first { $0.encounterId == encounter.encounterId }?
            .judgment != nil
        guard landed else { return nil }

        let answer = Answer(encounterId: encounter.encounterId, conceptName: concept.name, verdict: verdict)
        offerUndo(answer)
        return answer
    }

    /// Take the last answer back.
    @discardableResult
    public func undo() async -> Bool {
        guard let answer = lastAnswer else { return false }
        expiry?.cancel()
        undoable = nil
        lastAnswer = nil
        return await store.retract(answer.encounterId)
    }

    private func offerUndo(_ answer: Answer) {
        expiry?.cancel()
        undoable = answer
        lastAnswer = answer
        let window = undoWindow
        expiry = Task { [weak self] in
            try? await Task.sleep(for: window)
            guard !Task.isCancelled else { return }
            // Only clear the answer this timer was started for. A second answer
            // inside the window replaces the first and restarts the clock.
            if self?.undoable == answer { self?.undoable = nil }
        }
    }
}
