//  TrayPopover.swift
//  UnrotMac
//
//  After the MenuBar artboard: what a left click on the ring shows. The top
//  gap and its two answers, what is next, the handful of things worth doing
//  from the menu bar, and a line saying what is sent where.
//
//  It answers one gap at a time and never shows a list. The list is the
//  window's job; this is for "one thing, while I'm here".

import SwiftUI
import UnrotKit

struct TrayPopover: View {
    let store: SurfaceStore
    let core: CoreProcess
    let quick: QuickAccept
    let watcher: Watcher
    let router: Router
    let openMain: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text(title).font(.system(size: 14, weight: .semibold))
                    Spacer()
                    StatusPill(watcher: watcher, core: core)
                }
                content
                if let answer = quick.undoable {
                    UndoStrip(answer: answer) { Task { await quick.undo() } }
                }
            }
            .padding(16)

            if let next = upNext {
                Divider()
                HStack(spacing: 8) {
                    Circle().fill(Color.bucketOpen).frame(width: 7, height: 7)
                    Text(next.name).font(.system(size: 13))
                    Spacer()
                    Text("next").font(.system(size: 11.5)).foregroundStyle(Color.inkFaint)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
            }

            Divider()
            VStack(spacing: 0) {
                MenuRow(title: "Open unrot", shortcut: "⌘0", action: openMain)
                MenuRow(title: watcher.paused ? "Resume watching" : "Pause watching") { watcher.paused.toggle() }
                SettingsLink {
                    MenuRowLabel(title: "Settings…", shortcut: "⌘,")
                }
                .buttonStyle(.plain)
            }
            .padding(.vertical, 6)

            if let spent {
                HStack {
                    Text(spent.label)
                    Spacer()
                    Text(spent.value).monospacedDigit()
                }
                .font(.system(size: 11.5))
                .foregroundStyle(Color.inkSoft)
                .padding(.horizontal, 16)
                .padding(.bottom, 8)
            }

            HStack(spacing: 6) {
                Image(systemName: "lock").font(.system(size: 10))
                Text(watcher.autoAnalyse
                     ? "Finished sessions are sent to your model for analysis."
                     : "Nothing is sent anywhere until you ask.")
            }
            .font(.system(size: 11))
            .foregroundStyle(Color.inkFaint)
            .padding(.horizontal, 16)
            .padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.sunk)
        }
        .frame(width: 340)
        .background(Color.paper)
    }

    private var title: String {
        if store.state == .failed { return "unrot" }
        let n = store.waitingCount
        return n == 0 ? "Nothing waiting" : "\(n) waiting on you"
    }

    /// What analysis has cost: this run while one is going, else this week.
    /// Hidden until something has been spent -- a row reading "$0.00" on a
    /// fresh install would be a number about nothing.
    private var spent: (label: String, value: String)? {
        if watcher.isRunning, let batch = watcher.batchSpent, batch.calls > 0 {
            return ("This run", batch.text)
        }
        if let week = watcher.spentThisWeek, week.calls > 0 {
            return ("This week", week.text)
        }
        return nil
    }

    private var upNext: Concept? {
        let waiting = store.concepts(in: .open)
        return waiting.count > 1 ? waiting[1] : nil
    }

    @ViewBuilder
    private var content: some View {
        if store.state == .failed {
            // The only alarm colour in the popover, for the one state the
            // server cannot send.
            VStack(alignment: .leading, spacing: 8) {
                Text("unrot-core isn't running")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Color.alarm)
                Text("This is not an empty list — it is an unanswered question.")
                    .font(.system(size: 12.5))
                    .foregroundStyle(Color.inkSoft)
                Button("Restart the core") { core.restart() }.buttonStyle(UnrotButton())
            }
        } else if let (concept, encounter) = store.nextWaiting {
            VStack(alignment: .leading, spacing: 8) {
                Text(concept.name).font(.system(size: 17, weight: .semibold))
                if let paraphrase = encounter.paraphrase {
                    Text(paraphrase)
                        .font(.system(size: 12.5))
                        .foregroundStyle(Color.inkSoft)
                        .lineLimit(4)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let provenance = encounter.provenance {
                    Text(provenance)
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(Color.inkFaint)
                }
                QuickKeys(busy: store.busy.contains(encounter.encounterId), showKeys: false) { verdict in
                    Task { await quick.answer(concept: concept, encounter: encounter, verdict) }
                }
                .padding(.top, 4)
                if encounter.sessionId != nil {
                    Button("Show the moment in the window") {
                        router.moment = .init(conceptId: concept.conceptId, encounterId: encounter.encounterId)
                        openMain()
                    }
                    .buttonStyle(LinkButton())
                }
            }
        } else if let surface = store.surface {
            Text(surface.detail)
                .font(.system(size: 12.5))
                .foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            // Not loaded yet. The core may simply be starting, which is not a
            // failure and must not be drawn as one.
            Text(core.status.isUp ? "Reading the log…" : "Starting the core…")
                .font(.system(size: 12.5))
                .foregroundStyle(Color.inkFaint)
        }
    }
}

private struct MenuRow: View {
    let title: String
    var shortcut: String? = nil
    let action: () -> Void

    var body: some View {
        Button(action: action) { MenuRowLabel(title: title, shortcut: shortcut) }
            .buttonStyle(.plain)
    }
}

private struct MenuRowLabel: View {
    let title: String
    var shortcut: String? = nil

    var body: some View {
        HStack {
            Text(title).font(.system(size: 13))
            Spacer()
            if let shortcut {
                Text(shortcut).font(.system(size: 12)).foregroundStyle(Color.inkFaint)
            }
        }
        .foregroundStyle(Color.inkPrimary)
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
        .contentShape(Rectangle())
    }
}

/// The same two keys everywhere they appear: D for "I didn't know this", K for
/// "I knew it". Plain keys rather than chords, because the whole point is that
/// answering costs nothing -- the undo is what makes that safe.
struct QuickKeys: View {
    let busy: Bool
    /// The keycaps teach the keys where there is room; the popover has none.
    var showKeys = true
    let answer: (Verdict) -> Void

    var body: some View {
        HStack(spacing: 8) {
            Button { answer(.confirm) } label: {
                HStack(spacing: 6) { Text("I didn't know this").lineLimit(1); if showKeys { Keycap(key: "D", inverted: true) } }
            }
            .keyboardShortcut("d", modifiers: [])
            .buttonStyle(UnrotButton(weight: .primary, fill: true))
            Button { answer(.dismiss) } label: {
                HStack(spacing: 6) { Text("I knew it").lineLimit(1); if showKeys { Keycap(key: "K") } }
            }
            .keyboardShortcut("k", modifiers: [])
            .buttonStyle(UnrotButton(fill: true))
        }
        .disabled(busy)
    }
}

/// Eight seconds to take it back. A confirmation dialog would tax every correct
/// answer to guard against the rare wrong one; this costs nothing when you were
/// right, and the undo is a correcting event, so the mis-key stays in the log.
struct UndoStrip: View {
    let answer: QuickAccept.Answer
    let undo: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "checkmark").foregroundStyle(Color.bucketLearning)
            VStack(alignment: .leading, spacing: 1) {
                Text(answer.sentence)
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundStyle(Color.bucketLearning)
                Text("Stays for eight seconds, then commits.")
                    .font(.system(size: 11))
                    .foregroundStyle(Color.inkSoft)
            }
            Spacer(minLength: 6)
            Button(action: undo) {
                HStack(spacing: 5) { Text("Undo"); Keycap(key: "⌘Z") }
            }
            .keyboardShortcut("z", modifiers: .command)
            .buttonStyle(LinkButton())
        }
        .padding(10)
        .background(Color.bucketLearningBG, in: RoundedRectangle(cornerRadius: 8))
    }
}
