//  GapCardView.swift
//  UnrotMac
//
//  One concept, after the Main artboard. One card per concept rather than a row
//  per encounter: one answer settles the concept (read.py's `bucket_for`), so a
//  concept met twice asks its question once and says "seen twice".
//
//  The card renders whichever bucket the server put the concept in. What it
//  offers follows from that: a waiting card asks the one question; a card you
//  said you didn't know offers the check and something to read.

import SwiftUI
import UnrotKit

struct GapCardView: View {
    let concept: Concept
    @Bindable var store: SurfaceStore
    let quick: QuickAccept
    let router: Router
    var isFocused = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header

            if let paraphrase = concept.lead?.paraphrase {
                Text(paraphrase)
                    .font(.system(size: 13.5))
                    .foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }

            if let provenance = concept.lead?.provenance {
                Text(provenance)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(Color.inkFaint)
            }

            actions.padding(.top, 2)

            ForEach(concept.material) { material in
                MaterialView(material: material)
            }
            if let error = store.materialState(concept.conceptId).error {
                // A refusal is the provenance gate working, so it stays on the
                // card in the server's own words, titled by which gate it was.
                let recursed = store.materialState(concept.conceptId).status == 409
                RefusalNote(
                    text: error,
                    title: recursed ? "Refused — would recurse" : "Refused — not grounded",
                    tint: recursed ? .bucketLearning : .bucketOpen,
                    wash: recursed ? .bucketLearningBG : .bucketOpenBG
                )
            }
        }
        .padding(16)
        .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(isFocused ? Color.link.opacity(0.7) : Color.rule, lineWidth: isFocused ? 1.5 : 1)
        )
        .opacity(store.isBusy(concept) ? 0.55 : 1)
        .animation(.easeOut(duration: 0.12), value: isFocused)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(concept.name)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(Color.inkPrimary)
                .textSelection(.enabled)
            if concept.typedIn {
                // Provenance, never ranking: styled as a label, not a lesser card.
                Pip(text: "you added this one", tint: .bucketLearning, wash: .bucketLearningBG)
            } else if concept.bucket == .open {
                Pip(text: "waved through", tint: .bucketOpen, wash: .bucketOpenBG)
            }
            Spacer()
            Text(corner)
                .font(.system(size: 11.5))
                .foregroundStyle(Color.inkFaint)
        }
    }

    /// Top right: how often it came up while it waits; how the check went once
    /// it is being learned.
    private var corner: String {
        if concept.bucket == .open { return concept.seen }
        if let level = concept.latestLevel { return "answered · \(level.label.lowercased())" }
        return concept.bucket == .learning ? "no answer yet" : concept.seen
    }

    @ViewBuilder
    private var actions: some View {
        HStack(spacing: 8) {
            if concept.bucket == .open, let encounter = concept.unanswered {
                Button { answer(encounter, .confirm) } label: {
                    HStack(spacing: 6) { Text("I didn't know this"); if isFocused { Keycap(key: "D", inverted: true) } }
                }
                .buttonStyle(UnrotButton(weight: .primary))
                Button { answer(encounter, .dismiss) } label: {
                    HStack(spacing: 6) { Text("I knew it"); if isFocused { Keycap(key: "K") } }
                }
                .buttonStyle(UnrotButton())
            } else if concept.bucket != .closed {
                Button("Explain it") { router.check = .init(conceptId: concept.conceptId) }
                    .buttonStyle(UnrotButton(weight: concept.bucket == .learning ? .primary : .secondary))
                MaterialMenu(concept: concept, store: store)
            }

            if store.materialState(concept.conceptId).busy {
                ProgressView().controlSize(.small)
            }
            Spacer()
            if let encounter = concept.lead, encounter.sessionId != nil {
                Button("Show the moment →") {
                    router.moment = .init(conceptId: concept.conceptId, encounterId: encounter.encounterId)
                }
                .buttonStyle(LinkButton())
            }
        }
    }

    private func answer(_ encounter: Encounter, _ verdict: Verdict) {
        Task { await quick.answer(concept: concept, encounter: encounter, verdict) }
    }
}

/// "Make something to read", with its two formats. Neither is the degraded one.
struct MaterialMenu: View {
    let concept: Concept
    let store: SurfaceStore

    var body: some View {
        Menu {
            Button("Written, with sources") {
                Task { await store.makeMaterial(conceptId: concept.conceptId, format: .textual) }
            }
            Button("Sources only — writes nothing") {
                Task { await store.makeMaterial(conceptId: concept.conceptId, format: .sourcesOnly) }
            }
        } label: {
            Text("Make something to read")
        }
        .menuStyle(.button)
        .buttonStyle(UnrotButton())
        .fixedSize()
        .disabled(store.materialState(concept.conceptId).busy)
    }
}

struct RefusalNote: View {
    let text: String
    var title: String? = nil
    var tint: Color = .bucketOpen
    var wash: Color = .bucketOpenBG

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let title {
                Text(title).font(.system(size: 12.5, weight: .semibold)).foregroundStyle(tint)
            }
            Text(text)
                .font(.system(size: 12.5))
                .foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(wash, in: RoundedRectangle(cornerRadius: 8))
    }
}
