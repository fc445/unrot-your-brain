//  CheckView.swift
//  UnrotMac
//
//  The comprehension check, and the result of answering it.
//
//  The question is fetched from `/api/concepts/{id}/check` and displayed
//  exactly as sent. It is never composed here. The words on screen and the
//  words stored against the answer have to be the same string, or re-grading
//  an old answer against a new rubric silently stops being valid -- and it
//  stops being valid without anything looking wrong.

import SwiftUI
import UnrotKit

struct CheckView: View {
    let conceptId: String
    @Bindable var store: SurfaceStore
    let onClose: () -> Void

    @State private var check: Check?
    @State private var text = ""
    @State private var submitting = false
    @State private var loadError: String?
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let check {
                Text(check.promptText)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Color.inkPrimary)
                    .fixedSize(horizontal: false, vertical: true)

                TextEditor(text: $text)
                    .font(.system(size: 13))
                    .scrollContentBackground(.hidden)
                    .frame(minHeight: 88)
                    .padding(8)
                    .background(Color.card, in: RoundedRectangle(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.ruleStrong, lineWidth: 1))
                    .focused($focused)

                HStack(spacing: 8) {
                    Button("Submit") { submit() }
                        .buttonStyle(.borderedProminent)
                        .disabled(trimmed.isEmpty || submitting)
                    Button("Cancel", action: onClose)
                        .buttonStyle(.bordered)
                    if submitting { ProgressView().controlSize(.small) }
                    Spacer()
                    Text(check.promptVersion)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(Color.inkFaint)
                }
                .font(.system(size: 12))
            } else if let loadError {
                RefusalNote(text: loadError)
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
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

    private var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

    private func submit() {
        submitting = true
        Task {
            // The answer is stored before grading is attempted, server-side, so
            // a grader that is down costs the level and never the text.
            _ = await store.submitExplanation(conceptId: conceptId, text: trimmed)
            submitting = false
            onClose()
        }
    }
}

struct CheckResultView: View {
    let graded: Graded
    let onDismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                if let level = graded.explanation.level {
                    Pip(text: level.label, tint: level.tint, wash: .sunk)
                }
                if !graded.graded {
                    // Never "you failed". The check did not run.
                    Pip(text: "not graded", tint: .inkFaint, wash: .sunk)
                }
                Spacer()
                Button("Dismiss", action: onDismiss).buttonStyle(.link).font(.system(size: 11))
            }

            if !graded.graded {
                Text(
                    "Your answer is saved. No grader was available, so it has not been "
                    + "levelled yet — it can be graded later from exactly this text."
                )
                .font(.system(size: 12))
                .foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
            }

            if let reasoning = graded.explanation.reasoning, !reasoning.isEmpty {
                Text(reasoning)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Absent means "not measured", not "flat". Three zero-height bars
            // would be a claim the grader never made.
            if let distribution = graded.explanation.probabilities {
                SoloMeter(distribution: distribution, chosen: graded.explanation.level)
            }

            if let confidence = graded.explanation.confidence {
                Text("confidence \(confidence, format: .number.precision(.fractionLength(2)))")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(Color.inkFaint)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
    }
}

/// The distribution across the three SOLO levels, drawn in order.
///
/// It is shown rather than collapsed to a single label because the
/// `listed -> causal` threshold is expected to move, and a distribution is
/// what makes moving it possible without re-grading anything.
private struct SoloMeter: View {
    let distribution: [String: Double]
    let chosen: SoloLevel?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(SoloLevel.ordered, id: \.rawValue) { level in
                let value = distribution[level.rawValue] ?? 0
                HStack(spacing: 8) {
                    Text(level.label)
                        .font(.system(size: 10))
                        .foregroundStyle(level == chosen ? Color.inkPrimary : Color.inkFaint)
                        .frame(width: 56, alignment: .leading)
                    GeometryReader { geometry in
                        ZStack(alignment: .leading) {
                            Capsule().fill(Color.rule)
                            Capsule()
                                .fill(level.tint.opacity(level == chosen ? 1 : 0.4))
                                .frame(width: max(2, geometry.size.width * value))
                        }
                    }
                    .frame(height: 6)
                    Text(value, format: .number.precision(.fractionLength(2)))
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(Color.inkFaint)
                        .frame(width: 34, alignment: .trailing)
                }
            }
        }
    }
}
