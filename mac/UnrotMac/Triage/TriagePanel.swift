//  TriagePanel.swift
//  UnrotMac
//
//  ⌥⌘J from anywhere: one gap at a time, two keys, and it closes itself when
//  there is nothing left.
//
//  A non-activating panel, so it takes the keyboard without taking the screen.
//  The app you were in stays frontmost; you answer three gaps and carry on.

import AppKit
import SwiftUI
import UnrotKit

@MainActor
final class TriagePanel: NSObject, NSWindowDelegate {
    private var panel: NSPanel?
    private let store: SurfaceStore
    private let quick: QuickAccept

    init(store: SurfaceStore, quick: QuickAccept) {
        self.store = store
        self.quick = quick
    }

    func toggle() {
        if let panel, panel.isVisible { close() } else { show() }
    }

    func show() {
        Task { await store.load() }
        let panel = panel ?? build()
        self.panel = panel
        panel.center()
        panel.makeKeyAndOrderFront(nil)
    }

    func close() {
        panel?.orderOut(nil)
    }

    private func build() -> NSPanel {
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 380, height: 250),
            styleMask: [.titled, .closable, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        panel.title = "Triage"
        panel.titlebarAppearsTransparent = true
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = false
        panel.isReleasedWhenClosed = false
        panel.delegate = self
        panel.contentView = NSHostingView(
            rootView: TriageView(store: store, quick: quick) { [weak self] in self?.close() }
        )
        return panel
    }
}

private struct TriageView: View {
    let store: SurfaceStore
    let quick: QuickAccept
    let finished: () -> Void

    /// Nothing to answer and nothing to take back. Kept open through the undo
    /// window after the last answer, so the last one is as undoable as the rest.
    private var done: Bool { store.nextWaiting == nil && quick.undoable == nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("Triage").font(.display(17))
                Spacer()
                Text(store.waitingCount == 1 ? "1 left" : "\(store.waitingCount) left")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Color.inkFaint)
            }

            if let (concept, encounter) = store.nextWaiting {
                GapPeek(concept: concept, encounter: encounter)
                QuickKeys(busy: store.busy.contains(encounter.encounterId)) { verdict in
                    Task { await quick.answer(concept: concept, encounter: encounter, verdict) }
                }
            } else if quick.undoable != nil {
                Text("That was the last one.")
                    .font(.system(size: 13))
                    .foregroundStyle(Color.inkSoft)
            } else {
                Text(store.surface?.headline ?? "Nothing waiting on you.")
                    .font(.system(size: 13))
                    .foregroundStyle(Color.inkSoft)
            }

            Spacer(minLength: 0)

            if let answer = quick.undoable {
                UndoStrip(answer: answer) { Task { await quick.undo() } }
            }

            HStack {
                Text("Esc to close")
                    .font(.system(size: 10))
                    .foregroundStyle(Color.inkFaint)
                Spacer()
                Button("Close", action: finished)
                    .keyboardShortcut(.cancelAction)
                    .buttonStyle(.link)
                    .font(.system(size: 11))
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 30)
        .padding(.bottom, 12)
        .frame(width: 380, height: 250)
        .background(Color.paper)
        // Closes on the transition to empty, not on opening empty: opening the
        // panel with nothing waiting should say so, not vanish.
        .onChange(of: done) { _, isDone in
            if isDone { finished() }
        }
    }
}
