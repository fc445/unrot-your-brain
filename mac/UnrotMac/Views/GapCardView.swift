//  GapCardView.swift
//  UnrotMac
//
//  One concept, its encounters, and everything you can do about it.
//
//  A port of ui/src/components/GapCard.tsx. The card renders whichever bucket
//  the server put the concept in; it never works one out.

import SwiftUI
import UnrotKit

struct GapCardView: View {
    let concept: Concept
    @Bindable var store: SurfaceStore
    let quick: QuickAccept
    /// The encounter K and D will answer, if it is on this card.
    let nextEncounterId: String?

    @State private var showingCheck = false
    @State private var openMoment: MomentRequest?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header

            ForEach(concept.encounters) { encounter in
                EncounterRow(
                    encounter: encounter,
                    busy: store.busy.contains(encounter.encounterId),
                    isNext: encounter.encounterId == nextEncounterId,
                    onJudge: { verdict in
                        // Through quick accept even for a click, so every answer
                        // gets the same undo regardless of how it was given.
                        Task { await quick.answer(concept: concept, encounter: encounter, verdict) }
                    },
                    onMoment: { openMoment = MomentRequest(id: encounter.encounterId) }
                )
            }

            if let graded = store.justGraded, graded.concept?.conceptId == concept.conceptId {
                CheckResultView(graded: graded) { store.justGraded = nil }
            }

            if showingCheck {
                CheckView(conceptId: concept.conceptId, store: store) {
                    showingCheck = false
                }
            }

            actions

            ForEach(concept.material) { material in
                MaterialView(material: material)
            }

            if let error = store.materialState(concept.conceptId).error {
                // A refusal is the provenance gate working, so it stays here on
                // the card in the server's own words rather than taking over
                // the page as though the core had broken.
                RefusalNote(text: error)
            }
        }
        .padding(16)
        .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.rule, lineWidth: 1))
        .opacity(store.isBusy(concept) ? 0.55 : 1)
        .sheet(item: $openMoment) { request in
            MomentSheet(encounterId: request.id, store: store) { openMoment = nil }
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(concept.name)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Color.inkPrimary)
                .textSelection(.enabled)

            Pip(text: concept.gapType, tint: .inkFaint, wash: .sunk)

            if let level = concept.latestLevel {
                Pip(text: level.label, tint: level.tint, wash: concept.bucket.wash)
            }

            Spacer()

            if concept.encounterCount > 1 {
                Text("\(concept.encounterCount) encounters")
                    .font(.system(size: 11))
                    .foregroundStyle(Color.inkFaint)
            }
        }
    }

    @ViewBuilder
    private var actions: some View {
        let busy = store.materialState(concept.conceptId).busy
        HStack(spacing: 8) {
            if concept.bucket != .closed {
                Button(showingCheck ? "Hide the check" : "Answer the check") {
                    showingCheck.toggle()
                }
                .buttonStyle(.bordered)
            }

            Menu("Make me something") {
                Button("Written, with sources") {
                    Task { await store.makeMaterial(conceptId: concept.conceptId, format: .textual) }
                }
                Button("Sources only") {
                    Task { await store.makeMaterial(conceptId: concept.conceptId, format: .sourcesOnly) }
                }
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .disabled(busy)

            if busy {
                ProgressView().controlSize(.small)
            }
            Spacer()
        }
        .font(.system(size: 12))
    }
}

private struct EncounterRow: View {
    let encounter: Encounter
    let busy: Bool
    /// Whether K and D answer this one. Shown, so the keys are never a guess.
    let isNext: Bool
    let onJudge: (Verdict) -> Void
    let onMoment: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 8) {
                Text(encounter.paraphrase ?? "—")
                    .font(.system(size: 13))
                    .foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                // Provenance, never ranking: a `manual` gap is styled exactly
                // like a detected one, because it is exactly as real.
                Pip(text: encounter.source, tint: .inkFaint, wash: .sunk)
            }

            HStack(spacing: 8) {
                if let judgment = encounter.judgment {
                    Text(judgment == "confirmed" ? "You said you didn't know this" : "You knew this")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Color.inkFaint)
                } else {
                    Button { onJudge(.confirm) } label: { KeyHint(label: "I didn't know this", key: isNext ? "D" : nil) }
                        .buttonStyle(.borderedProminent)
                    Button { onJudge(.dismiss) } label: { KeyHint(label: "I knew this", key: isNext ? "K" : nil) }
                        .buttonStyle(.bordered)
                }

                Button("Show me the moment") { onMoment() }
                    .buttonStyle(.link)

                if busy { ProgressView().controlSize(.small) }
                Spacer()
            }
            .font(.system(size: 12))
            .disabled(busy)
        }
        .padding(.leading, 10)
        .overlay(alignment: .leading) {
            Rectangle().fill(Color.rule).frame(width: 2)
        }
    }
}

struct Pip: View {
    let text: String
    let tint: Color
    let wash: Color

    var body: some View {
        Text(text)
            .font(.system(size: 10, weight: .medium))
            .foregroundStyle(tint)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(wash, in: Capsule())
    }
}

struct RefusalNote: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "hand.raised")
                .font(.system(size: 11))
                .foregroundStyle(Color.bucketOpen)
            Text(text)
                .font(.system(size: 12))
                .foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.bucketOpenBG, in: RoundedRectangle(cornerRadius: 8))
    }
}

struct MomentRequest: Identifiable, Hashable {
    let id: String
}

/// The moment is fetched when it is asked for, not with the surface.
///
/// `/api/surface` already carries every concept, encounter, explanation and
/// piece of material; adding forty transcript lines per encounter to that
/// would make the first paint pay for something almost nobody opens.
private struct MomentSheet: View {
    let encounterId: String
    let store: SurfaceStore
    let onClose: () -> Void

    @State private var moment: Moment?
    @State private var failure: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("The moment").font(.display(17))
                Spacer()
                Button("Done", action: onClose).keyboardShortcut(.defaultAction)
            }
            .padding(14)

            Divider()

            ScrollView {
                if let moment {
                    MomentView(moment: moment)
                } else if let failure {
                    RefusalNote(text: failure).padding(14)
                } else {
                    ProgressView().controlSize(.small).padding(30)
                }
            }
        }
        .frame(width: 560, height: 460)
        .background(Color.paper)
        .task {
            do {
                moment = try await store.moment(encounterId: encounterId)
            } catch let error as APIError {
                failure = error.message
            } catch {
                failure = "Could not replay that moment."
            }
        }
    }
}

private struct KeyHint: View {
    let label: String
    let key: String?

    var body: some View {
        HStack(spacing: 6) {
            Text(label)
            if let key {
                Text(key)
                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .padding(.horizontal, 4)
                    .padding(.vertical, 1)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 3))
            }
        }
    }
}
