//  CapturePanel.swift
//  UnrotMac
//
//  Where a selection becomes a gap, and where the resolver's opinion about it
//  can be argued with before you move on.
//
//  The same panel serves "Add a Gap…" from the ring, where there is no
//  selection and you type the term yourself.

import AppKit
import Observation
import SwiftUI
import UnrotKit

@MainActor
@Observable
final class CaptureModel {
    enum Phase: Equatable {
        case composing
        case sending
        case filed(Submitted)
        case splitOut(Corrected)
        case failed(String)
    }

    var text = ""
    var ownWords = ""
    var seenIn: String?
    var rememberSource = true
    private(set) var fromSelection = false
    private(set) var phase: Phase = .composing

    private let store: SurfaceStore

    init(store: SurfaceStore) {
        self.store = store
    }

    func reset(selection: String?, from app: String?) {
        text = selection ?? ""
        fromSelection = selection != nil
        ownWords = ""
        seenIn = app
        rememberSource = true
        phase = .composing
    }

    var canSend: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && phase == .composing
    }

    func send() async {
        guard canSend else { return }
        phase = .sending
        do {
            let submitted = try await store.submit(
                text: text,
                ownWords: ownWords.isEmpty ? nil : ownWords,
                seenIn: rememberSource ? seenIn : nil
            )
            phase = .filed(submitted)
        } catch let error as APIError {
            phase = .failed(error.message)
        } catch {
            phase = .failed("That did not save.")
        }
    }

    /// "No -- this is new." A correction the core records as ground truth, and
    /// a repair that gives the encounter a concept of its own.
    func itIsNew() async {
        guard case .filed(let submitted) = phase else { return }
        phase = .sending
        do {
            let corrected = try await store.splitOut(
                submitted,
                reasoning: "Captured from a selection; the user said this is not \(submitted.canonicalName)."
            )
            phase = .splitOut(corrected)
        } catch let error as APIError {
            phase = .failed(error.message)
        } catch {
            phase = .failed("That correction did not save.")
        }
    }
}

@MainActor
final class CapturePanel: NSObject, NSWindowDelegate {
    private var panel: NSPanel?
    private let model: CaptureModel

    init(store: SurfaceStore) {
        model = CaptureModel(store: store)
    }

    /// From the Services menu, with the selection.
    func open(selection: String, from app: String?) {
        model.reset(selection: selection, from: app)
        present()
    }

    /// From "Add a Gap…", with nothing selected.
    func openBlank() {
        model.reset(selection: nil, from: nil)
        present()
    }

    private func present() {
        let panel = panel ?? build()
        self.panel = panel
        panel.center()
        panel.makeKeyAndOrderFront(nil)
    }

    private func close() { panel?.orderOut(nil) }

    private func build() -> NSPanel {
        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 300),
            styleMask: [.titled, .closable, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        panel.title = "Add to unrot"
        panel.titlebarAppearsTransparent = true
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = false
        panel.isReleasedWhenClosed = false
        panel.delegate = self
        panel.contentView = NSHostingView(
            rootView: CaptureView(model: model) { [weak self] in self?.close() }
        )
        return panel
    }
}

private struct CaptureView: View {
    @Bindable var model: CaptureModel
    let done: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Add to unrot").font(.display(17))

            switch model.phase {
            case .composing, .sending:
                composing
            case .filed(let submitted):
                FiledView(submitted: submitted, model: model, done: done)
            case .splitOut(let corrected):
                VStack(alignment: .leading, spacing: 8) {
                    Text("Filed as a gap of its own: **\(corrected.concept?.name ?? model.text)**.")
                        .font(.system(size: 13))
                    Text("The resolver's call is kept, with your correction beside it.")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.inkFaint)
                }
                Spacer(minLength: 0)
                footer(primary: "Done", action: done)
            case .failed(let message):
                RefusalNote(text: message)
                Spacer(minLength: 0)
                footer(primary: "Close", action: done)
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 30)
        .padding(.bottom, 14)
        .frame(width: 420)
        .frame(minHeight: 260)
        .background(Color.paper)
    }

    @ViewBuilder
    private var composing: some View {
        if model.fromSelection {
            Text("“\(model.text)”")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(Color.inkPrimary)
                .lineLimit(4)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            TextField("A term you met and didn't follow", text: $model.text)
                .textFieldStyle(.roundedBorder)
        }

        TextField("In your own words — optional", text: $model.ownWords, axis: .vertical)
            .textFieldStyle(.roundedBorder)
            .lineLimit(1...3)

        if let app = model.seenIn {
            Toggle("Remember it came from \(app)", isOn: $model.rememberSource)
                .toggleStyle(.checkbox)
                .font(.system(size: 12))
        }

        Text(model.fromSelection
             ? "unrot receives the selection and nothing else — not the page, not the document."
             : "Tip: select text in any app, then right-click › Services › Add to unrot.")
            .font(.system(size: 11))
            .foregroundStyle(Color.inkFaint)
            .fixedSize(horizontal: false, vertical: true)

        Spacer(minLength: 0)

        HStack {
            if model.phase == .sending { ProgressView().controlSize(.small) }
            Spacer()
            Button("Cancel", action: done).keyboardShortcut(.cancelAction)
            Button("Add") { Task { await model.send() } }
                .keyboardShortcut(.defaultAction)
                .disabled(!model.canSend)
        }
    }

    private func footer(primary: String, action: @escaping () -> Void) -> some View {
        HStack {
            Spacer()
            Button(primary, action: action).keyboardShortcut(.defaultAction)
        }
    }
}

/// What the resolver made of it -- and, when it matched something, the chance
/// to say it got that wrong. The judgment is stored as its own event precisely
/// so it can be argued with, so the UI has to offer the argument.
private struct FiledView: View {
    let submitted: Submitted
    let model: CaptureModel
    let done: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if submitted.decision == "new" {
                Text("New gap: **\(submitted.canonicalName)**")
                    .font(.system(size: 13))
            } else {
                Text("This looks like **\(submitted.canonicalName)**, which you've met before.")
                    .font(.system(size: 13))
            }

            Text(submitted.reasoning)
                .font(.system(size: 12))
                .italic()
                .foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)

            if let unavailable = submitted.modelUnavailable {
                RefusalNote(text: unavailable)
            }
        }

        Spacer(minLength: 0)

        HStack {
            Spacer()
            if submitted.isArguable {
                Button("No — this is new") { Task { await model.itIsNew() } }
                Button("That's it", action: done).keyboardShortcut(.defaultAction)
            } else {
                Button("Done", action: done).keyboardShortcut(.defaultAction)
            }
        }
    }
}
