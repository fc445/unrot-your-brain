//  MomentView.swift
//  UnrotMac
//
//  After the Moment artboard: the point of acceptance, replayed rather than
//  abstracted into a flag.
//
//  S4's local layer. It resolves against unrot's own retained copy, not
//  ~/.claude, which may have rotated the original away -- so this screen exists
//  only on the machine that captured the session, and it says so.
//
//  Three reasons it can be unavailable, all honest answers rather than errors,
//  and shown in the server's own words.

import AppKit
import SwiftUI
import UnrotKit

struct MomentSheet: View {
    let request: Router.Moment
    let store: SurfaceStore
    let quick: QuickAccept
    let onClose: () -> Void

    @State private var moment: Moment?
    @State private var failure: String?

    private var concept: Concept? {
        store.surface?.concepts.first { $0.conceptId == request.conceptId }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Button("← Back", action: onClose)
                    .buttonStyle(UnrotButton())
                    .keyboardShortcut(.cancelAction)
                Text(concept?.name ?? "")
                    .font(.system(size: 14, weight: .semibold))
                Spacer()
                Label("Local only", systemImage: "lock")
                    .font(.system(size: 11.5, weight: .medium))
                    .foregroundStyle(Color.inkSoft)
                    .padding(.horizontal, 9)
                    .padding(.vertical, 4)
                    .overlay(Capsule().stroke(Color.ruleStrong, lineWidth: 1))
            }
            .padding(.horizontal, 16)
            .frame(height: 52)
            .background(Color.sunk)
            Divider()

            HStack(alignment: .top, spacing: 20) {
                transcript
                side.frame(width: 320)
            }
            .padding(24)
        }
        .frame(width: 960, height: 640)
        .background(Color.paper)
        .task {
            do {
                moment = try await store.moment(encounterId: request.encounterId)
            } catch let error as APIError {
                failure = error.message
            } catch {
                failure = "Could not replay that moment."
            }
        }
    }

    // MARK: - Left: what was said

    private var transcript: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("The moment you waved it through")
                .font(.display(28))
                .foregroundStyle(Color.inkPrimary)
            if let meta {
                Text(meta)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(Color.inkFaint)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let moment, moment.resolvable {
                        ForEach(moment.turns) { turn in
                            TurnView(turn: turn, term: concept?.name, isSignal: turn.lineNo == signalLine)
                        }
                    } else if let reason = moment?.reason ?? failure {
                        Text(reason)
                            .font(.system(size: 13.5))
                            .foregroundStyle(Color.inkSoft)
                    } else {
                        ProgressView().controlSize(.small)
                    }
                }
                .padding(18)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.rule, lineWidth: 1))
            .padding(.top, 8)
        }
    }

    /// "session 4f1c9a20… · lines 214–231 · payments-api".
    private var meta: String? {
        guard let moment, let session = moment.sessionId else { return nil }
        var parts = ["session \(session.prefix(8))…"]
        if let start = moment.lineStart, let end = moment.lineEnd { parts.append("lines \(start)–\(end)") }
        if let repo = concept?.encounters.first(where: { $0.encounterId == request.encounterId })?.repo {
            parts.append(repo)
        }
        return parts.joined(separator: " · ")
    }

    /// The human turn that waved it through: the last one in the window.
    private var signalLine: Int? {
        moment?.turns.last { $0.role == "user" && !$0.isMeta }?.lineNo
    }

    // MARK: - Right: what to do about it

    private var side: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let concept, concept.bucket == .open, let encounter = concept.unanswered {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Do you know it?").font(.system(size: 14, weight: .semibold))
                    Text("Saying you didn't doesn't close the gap. It starts learning it.")
                        .font(.system(size: 12.5))
                        .foregroundStyle(Color.inkSoft)
                    Button("I didn't know this") { answer(concept, encounter, .confirm) }
                        .buttonStyle(UnrotButton(weight: .primary, fill: true))
                    Button("I knew it — bad flag") { answer(concept, encounter, .dismiss) }
                        .buttonStyle(UnrotButton(fill: true))
                    Text("A bad flag is kept, not deleted. It is what the detector gets tuned against.")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.inkFaint)
                }
                .padding(16)
                .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.rule, lineWidth: 1))
            }

            VStack(alignment: .leading, spacing: 8) {
                Label("Why this screen is Mac-only", systemImage: "lock")
                    .font(.system(size: 13, weight: .semibold))
                Text("This replays unrot's own retained copy, not ~/.claude, which may have rotated the original away. Raw transcripts never sync and never upload, so no other device can ever show this.")
                    .font(.system(size: 12))
                    .foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                if let copy {
                    Button("Reveal the copy in Finder") {
                        NSWorkspace.shared.activateFileViewerSelecting([copy])
                    }
                    .buttonStyle(LinkButton())
                }
            }
            .padding(16)
            .background(Color.sunk, in: RoundedRectangle(cornerRadius: 10))

            Spacer()
        }
    }

    private var copy: URL? {
        guard let session = moment?.sessionId else { return nil }
        let url = CoreProcess.unrotHome().appending(path: "raw/sessions/\(session).jsonl")
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    private func answer(_ concept: Concept, _ encounter: Encounter, _ verdict: Verdict) {
        Task {
            await quick.answer(concept: concept, encounter: encounter, verdict)
            onClose()
        }
    }
}

private struct TurnView: View {
    let turn: MomentTurn
    let term: String?
    let isSignal: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 6) {
                Eyebrow(text: "\(speaker) · line \(turn.lineNo)")
                // Injected text is not something the user said, and must never
                // be presented as though it were.
                if turn.isMeta { Pip(text: "injected, not typed", tint: .inkFaint, wash: .sunk) }
                if turn.isSidechain { Pip(text: "sidechain", tint: .inkFaint, wash: .sunk) }
            }
            if isSignal {
                VStack(alignment: .leading, spacing: 4) {
                    Text(turn.text)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Color.inkPrimary)
                    Text("No clarifying question. This turn is the signal — the term was load-bearing and it went by unexamined.")
                        .font(.system(size: 11.5))
                        .foregroundStyle(Color.bucketOpen)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.bucketOpenBG, in: RoundedRectangle(cornerRadius: 6))
                .overlay(alignment: .leading) { Rectangle().fill(Color.bucketOpen).frame(width: 3) }
            } else {
                Text(highlighted)
                    .font(.system(size: 13.5))
                    .foregroundStyle(turn.role == "user" ? Color.inkPrimary : Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
    }

    private var speaker: String {
        switch turn.role {
        case "user": "You"
        case "assistant": "Claude"
        default: turn.role
        }
    }

    /// The term, marked wherever it appears, so the eye lands on it.
    private var highlighted: AttributedString {
        var text = AttributedString(turn.text)
        guard let term, !term.isEmpty else { return text }
        var searchFrom = text.startIndex
        while let range = text[searchFrom...].range(of: term, options: .caseInsensitive) {
            text[range].backgroundColor = Color.bucketOpenBG
            text[range].foregroundColor = Color.inkPrimary
            text[range].font = .system(size: 13.5, weight: .semibold)
            searchFrom = range.upperBound
        }
        return text
    }
}
