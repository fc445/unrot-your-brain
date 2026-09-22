//  TrayPopover.swift
//  UnrotMac
//
//  What a left click on the ring shows: the top gap and two keys, or the
//  server's own sentence about why there is nothing to answer.
//
//  It answers one gap at a time and never shows a list. The list is the
//  window's job; this is for "one thing, while I'm here".

import SwiftUI
import UnrotKit

struct TrayPopover: View {
    let store: SurfaceStore
    let core: CoreProcess
    let quick: QuickAccept
    let openMain: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("unrot").font(.display(17))
                Spacer()
                if store.waitingCount > 0 {
                    Text("\(store.waitingCount) waiting")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Color.inkFaint)
                }
            }

            content

            if let answer = quick.undoable {
                UndoStrip(answer: answer) { Task { await quick.undo() } }
            }

            Divider()

            HStack {
                Button("Open unrot", action: openMain)
                    .buttonStyle(.link)
                Spacer()
            }
            .font(.system(size: 12))
        }
        .padding(14)
        .frame(width: 320)
        .background(Color.paper)
    }

    @ViewBuilder
    private var content: some View {
        if store.state == .failed {
            // The only alarm colour in the popover, and only for the one state
            // the server cannot send.
            VStack(alignment: .leading, spacing: 6) {
                Text("The core isn't running")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Color.alarm)
                Text(store.failure ?? "unrot is starting it again.")
                    .font(.system(size: 12))
                    .foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } else if let (concept, encounter) = store.nextWaiting {
            GapPeek(concept: concept, encounter: encounter)
            QuickKeys(busy: store.busy.contains(encounter.encounterId)) { verdict in
                Task { await quick.answer(concept: concept, encounter: encounter, verdict) }
            }
        } else if let surface = store.surface {
            // The server's words, for whichever of the calm states this is.
            VStack(alignment: .leading, spacing: 4) {
                Text(surface.headline).font(.system(size: 13, weight: .semibold))
                Text(surface.detail)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
        } else {
            // Not loaded yet. The core may simply be starting, which is not a
            // failure and must not be drawn as one.
            Text(core.status.isUp ? "Reading the log…" : "Starting the core…")
                .font(.system(size: 12))
                .foregroundStyle(Color.inkFaint)
        }
    }
}

struct GapPeek: View {
    let concept: Concept
    let encounter: Encounter

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text(concept.name)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Color.inkPrimary)
                Pip(text: encounter.source, tint: .inkFaint, wash: .sunk)
            }
            Text(encounter.paraphrase ?? "—")
                .font(.system(size: 12))
                .foregroundStyle(Color.inkSoft)
                .lineLimit(4)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.card, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.rule, lineWidth: 1))
    }
}

/// The same two keys everywhere they appear: D for "I didn't know this", K for
/// "I knew this". Plain keys rather than chords, because the whole point is
/// that answering costs nothing -- the undo is what makes that safe.
struct QuickKeys: View {
    let busy: Bool
    let answer: (Verdict) -> Void

    var body: some View {
        HStack(spacing: 8) {
            Button { answer(.confirm) } label: { Keyed(label: "I didn't know this", key: "D") }
                .keyboardShortcut("d", modifiers: [])
                .buttonStyle(.borderedProminent)
            Button { answer(.dismiss) } label: { Keyed(label: "I knew this", key: "K") }
                .keyboardShortcut("k", modifiers: [])
                .buttonStyle(.bordered)
            if busy { ProgressView().controlSize(.small) }
            Spacer(minLength: 0)
        }
        .font(.system(size: 12))
        .disabled(busy)
    }
}

private struct Keyed: View {
    let label: String
    let key: String

    var body: some View {
        HStack(spacing: 6) {
            Text(label)
            Text(key)
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .padding(.horizontal, 4)
                .padding(.vertical, 1)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 3))
        }
    }
}

/// Eight seconds to take it back. A confirmation dialog would tax every correct
/// answer to guard against the rare wrong one; this costs nothing when you were
/// right, and the undo is a correcting event, so the mis-key stays in the log.
struct UndoStrip: View {
    let answer: QuickAccept.Answer
    let undo: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Text(answer.sentence)
                .font(.system(size: 12))
                .foregroundStyle(Color.inkSoft)
                .lineLimit(2)
            Spacer(minLength: 4)
            Button("Undo", action: undo)
                .keyboardShortcut("z", modifiers: .command)
                .buttonStyle(.link)
                .font(.system(size: 12, weight: .medium))
        }
        .padding(8)
        .background(Color.sunk, in: RoundedRectangle(cornerRadius: 6))
    }
}
