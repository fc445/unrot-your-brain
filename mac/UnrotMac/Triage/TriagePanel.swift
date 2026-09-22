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
            contentRect: NSRect(x: 0, y: 0, width: 400, height: 340),
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
        // A hosting controller that sizes the panel to its content, so a
        // panel whose state changes grows and shrinks with it rather than
        // clipping or leaving a gap.
        let hosting = NSHostingController(rootView: TriageView(store: store, quick: quick) { [weak self] in self?.close() })
        hosting.sizingOptions = [.preferredContentSize]
        panel.contentViewController = hosting
        return panel
    }
}

struct TriageView: View {
    let store: SurfaceStore
    let quick: QuickAccept
    let finished: () -> Void

    /// Answered since the panel opened, for "1 of 3" and the closing line.
    @State private var answered = 0

    /// Nothing to answer and nothing to take back. Kept open through the undo
    /// window after the last answer, so the last one is as undoable as the rest.
    private var done: Bool { store.nextWaiting == nil && quick.undoable == nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("Triage").font(.display(18))
                Spacer()
                Text("Esc to close").font(.system(size: 11)).foregroundStyle(Color.inkFaint)
            }

            if let (concept, encounter) = store.nextWaiting {
                VStack(alignment: .leading, spacing: 8) {
                    Eyebrow(text: "\(answered + 1) of \(answered + store.waitingCount)", tint: .bucketOpen)
                    Text(concept.name).font(.system(size: 17, weight: .semibold))
                    Text(encounter.paraphrase ?? "")
                        .font(.system(size: 13))
                        .foregroundStyle(Color.inkSoft)
                        .lineLimit(4)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                    QuickKeys(busy: store.busy.contains(encounter.encounterId)) { verdict in
                        Task {
                            if await quick.answer(concept: concept, encounter: encounter, verdict) != nil {
                                answered += 1
                            }
                        }
                    }
                }
                .padding(14)
                .frame(maxWidth: .infinity, minHeight: 150, alignment: .topLeading)
                .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.rule, lineWidth: 1))
            } else if answered > 0 {
                VStack(spacing: 8) {
                    Image(nsImage: TrayGlyph.image(for: .clean))
                        .renderingMode(.template)
                        .resizable()
                        .frame(width: 26, height: 26)
                        .foregroundStyle(Color.bucketClosed)
                    Text("That's everything.")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Color.bucketClosed)
                    Text("\(answered) answered. The ring closes.")
                        .font(.system(size: 12.5))
                        .foregroundStyle(Color.inkSoft)
                }
                .frame(maxWidth: .infinity, minHeight: 150)
                .background(Color.bucketClosedBG, in: RoundedRectangle(cornerRadius: 10))
            } else {
                Text(store.surface?.headline ?? "Nothing waiting on you.")
                    .font(.system(size: 13.5))
                    .foregroundStyle(Color.inkSoft)
                    .frame(maxWidth: .infinity, minHeight: 150)
            }

            if let answer = quick.undoable {
                UndoStrip(answer: answer) { Task { if await quick.undo() { answered -= 1 } } }
            }

            Button("Close", action: finished)
                .keyboardShortcut(.cancelAction)
                .hidden()
                .frame(height: 0)
        }
        .padding(.horizontal, 16)
        .padding(.top, 30)
        .padding(.bottom, 14)
        .frame(width: 400)
        .background(Color.paper)
        // Closes on the transition to empty, not on opening empty: opening the
        // panel with nothing waiting should say so, not vanish.
        .onChange(of: done) { _, isDone in
            if isDone { finished() }
        }
    }
}
