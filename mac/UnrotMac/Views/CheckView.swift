//  CheckView.swift
//  UnrotMac
//
//  After the Check artboard: the comprehension check as its own screen.
//
//  The question is fetched from /api/concepts/{id}/check and shown exactly as
//  sent -- the words on screen and the words stored against the answer must be
//  the same string, or re-grading an old answer silently stops being valid.

import SwiftUI
import UnrotKit

struct CheckSheet: View {
    let conceptId: String
    let store: SurfaceStore
    let onClose: () -> Void

    @State private var check: Check?
    @State private var text = ""
    @State private var submitting = false
    @State private var loadError: String?
    @State private var result: Graded?
    @FocusState private var focused: Bool

    private var concept: Concept? {
        store.surface?.concepts.first { $0.conceptId == conceptId }
    }

    /// The most recent answer: the one just given, or the last one on record.
    private var latest: Explanation? { result?.explanation ?? concept?.explanations.last }

    var body: some View {
        HStack(alignment: .top, spacing: 28) {
            question.frame(maxWidth: .infinity)
            answerPanel.frame(width: 440)
        }
        .padding(28)
        .frame(width: 1000, height: 660)
        .background(Color.paper)
        .task {
            do {
                check = try await store.check(conceptId: conceptId)
                focused = true
            } catch let error as APIError {
                loadError = error.message
            } catch {
                loadError = "Could not load the question."
            }
        }
    }

    // MARK: - Left

    private var question: some View {
        VStack(alignment: .leading, spacing: 14) {
            Eyebrow(text: "The check")
            Text(check?.name ?? concept?.name ?? "")
                .font(.display(30))
                .foregroundStyle(Color.inkPrimary)

            if let check {
                VStack(alignment: .leading, spacing: 8) {
                    Text(check.promptText)
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(Color.inkPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("stored verbatim · prompt \(check.promptVersion)")
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(Color.inkFaint)
                }
                .padding(18)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.rule, lineWidth: 1))

                Text("Your answer").font(.system(size: 12.5, weight: .semibold))
                TextEditor(text: $text)
                    .font(.system(size: 14))
                    .scrollContentBackground(.hidden)
                    .padding(10)
                    .frame(height: 130)
                    .background(Color.card, in: RoundedRectangle(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.ruleStrong, lineWidth: 1))
                    .focused($focused)

                HStack(spacing: 8) {
                    Button("Submit") { submit() }
                        .buttonStyle(UnrotButton(weight: .primary))
                        .keyboardShortcut(.return, modifiers: .command)
                        .disabled(trimmed.isEmpty || submitting)
                    Button(result == nil ? "Not now" : "Done", action: onClose)
                        .buttonStyle(UnrotButton())
                        .keyboardShortcut(.cancelAction)
                    if submitting { ProgressView().controlSize(.small) }
                    Spacer()
                    Text("Saved before anything grades it")
                        .font(.system(size: 11.5))
                        .foregroundStyle(Color.inkFaint)
                }
            } else if let loadError {
                RefusalNote(text: loadError)
            } else {
                ProgressView().controlSize(.small)
            }

            Spacer(minLength: 0)

            (Text("It asks for causation on purpose. ").bold()
             + Text("A definition-shaped question gets definition-shaped answers, and the only boundary the rubric turns on is listed → causal. Old answers can be re-graded; a question you have moved on from cannot be re-asked."))
                .font(.system(size: 12))
                .foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
                .padding(14)
                .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
        }
    }

    // MARK: - Right

    @ViewBuilder
    private var answerPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let latest {
                if latest.level == nil || result?.graded == false {
                    NotGraded()
                } else {
                    GradedPanel(explanation: latest, isNew: result != nil)
                }
            }
            WhatHappens(level: latest?.level)
            Spacer(minLength: 0)
            Text("Every answer keeps the question it was given to. Change the wording later and both remain readable side by side.")
                .font(.system(size: 11))
                .foregroundStyle(Color.inkFaint)
        }
    }

    private var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

    private func submit() {
        submitting = true
        Task {
            // Stored before it is graded, server-side: a grader that is down
            // costs the level, never the words.
            result = await store.submitExplanation(conceptId: conceptId, text: trimmed)
            submitting = false
            if result != nil { text = "" }
        }
    }
}

/// The grade, as the canvas draws it: three labelled tiles, the distribution as
/// percentages, and the grader's reasoning.
private struct GradedPanel: View {
    let explanation: Explanation
    /// False when this is an answer already on record rather than the one just
    /// given -- the box on the left is empty, so the panel has to say which.
    var isNew = true

    private static let described: [SoloLevel: String] = [
        .isolated: "restates the term", .listed: "names the parts", .causal: "says why it holds",
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(isNew ? "Graded" : "Your last answer").font(.system(size: 14, weight: .semibold))
                Spacer()
                Text(stamp)
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(Color.inkFaint)
            }
            HStack(spacing: 6) {
                ForEach(SoloLevel.ordered, id: \.rawValue) { level in
                    let chosen = level == explanation.level
                    VStack(alignment: .leading, spacing: 2) {
                        Text(level.label).font(.system(size: 12.5, weight: .semibold))
                        Text(Self.described[level] ?? "").font(.system(size: 10.5))
                    }
                    .foregroundStyle(chosen ? level.tint : Color.inkSoft)
                    .padding(9)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(chosen ? level.tint.opacity(0.1) : Color.sunk, in: RoundedRectangle(cornerRadius: 7))
                    .overlay(RoundedRectangle(cornerRadius: 7).stroke(chosen ? level.tint : Color.clear, lineWidth: 1.5))
                }
            }
            // Absent means "not measured", not "flat": no bars rather than
            // three zeroes, which would be a claim the grader never made.
            if let distribution = explanation.probabilities {
                VStack(spacing: 6) {
                    ForEach(SoloLevel.ordered, id: \.rawValue) { level in
                        let value = distribution[level.rawValue] ?? 0
                        HStack(spacing: 10) {
                            Text(level.rawValue).font(.system(size: 11)).frame(width: 52, alignment: .leading)
                            GeometryReader { geometry in
                                ZStack(alignment: .leading) {
                                    Capsule().fill(Color.rule)
                                    Capsule()
                                        .fill(level.tint.opacity(level == explanation.level ? 1 : 0.45))
                                        .frame(width: max(3, geometry.size.width * value))
                                }
                            }
                            .frame(height: 7)
                            Text(value, format: .percent.precision(.fractionLength(0)))
                                .font(.system(size: 11, design: .monospaced))
                                .frame(width: 36, alignment: .trailing)
                        }
                        .foregroundStyle(Color.inkSoft)
                    }
                }
            }
            if let reasoning = explanation.reasoning, !reasoning.isEmpty {
                Text(reasoning)
                    .font(.system(size: 12.5))
                    .foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
            }
            Text("“\(explanation.rawText)”")
                .font(.system(size: 12))
                .italic()
                .foregroundStyle(Color.inkFaint)
                .lineLimit(3)
        }
        .padding(18)
        .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.rule, lineWidth: 1))
    }

    private var stamp: String {
        var parts: [String] = []
        if let grader = explanation.graderVersion { parts.append(grader) }
        if let confidence = explanation.confidence {
            parts.append("confidence \(confidence.formatted(.number.precision(.fractionLength(2))))")
        }
        return parts.joined(separator: " · ")
    }
}

/// The ungraded state is a normal one, and must never read as a failed check.
private struct NotGraded: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Stored, not graded").font(.system(size: 14, weight: .semibold))
            Text("No grader was available. Your words are the half that cannot be reconstructed, so they were committed first — this exact text can be graded later. You are never told you failed a check that never ran.")
                .font(.system(size: 12.5))
                .foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.ruleStrong, style: StrokeStyle(lineWidth: 1, dash: [4, 3]))
        )
    }
}

private struct WhatHappens: View {
    let level: SoloLevel?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("What happens to the gap").font(.system(size: 14, weight: .semibold))
            row(.listed, "Listed or below", "stays in To learn. Material is offered, not generated.")
            row(.causal, "Causal", "compiles to known and moves to Closed.")
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.rule, lineWidth: 1))
    }

    private func row(_ tintLevel: SoloLevel, _ name: String, _ rest: String) -> some View {
        let reached = level.map { ($0 == .causal) == (tintLevel == .causal) } ?? false
        return HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle().fill(tintLevel.tint).frame(width: 7, height: 7)
            (Text(name).bold() + Text(" — \(rest)"))
                .font(.system(size: 12.5))
                .foregroundStyle(reached ? Color.inkPrimary : Color.inkSoft)
        }
    }
}
