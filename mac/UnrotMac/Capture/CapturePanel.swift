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

    #if DEBUG
    /// For the snapshot run only: show a phase without a round trip.
    func showForSnapshot(_ phase: Phase) { self.phase = phase }
    #endif

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
            contentRect: NSRect(x: 0, y: 0, width: 440, height: 400),
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
        // A hosting controller that sizes the panel to its content, so a
        // panel whose state changes grows and shrinks with it rather than
        // clipping or leaving a gap.
        let hosting = NSHostingController(rootView: CaptureView(model: model) { [weak self] in self?.close() })
        hosting.sizingOptions = [.preferredContentSize]
        panel.contentViewController = hosting
        return panel
    }
}

struct CaptureView: View {
    @Bindable var model: CaptureModel
    let done: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Image(nsImage: TrayGlyph.image(for: .waiting(0)))
                    .renderingMode(.template)
                    .foregroundStyle(Color.inkPrimary)
                Text("Add a gap").font(.system(size: 15, weight: .semibold))
            }

            switch model.phase {
            case .composing, .sending:
                composing
            case .filed(let submitted):
                FiledView(submitted: submitted, model: model, done: done)
            case .splitOut(let corrected):
                Verdict(
                    title: "Filed as a gap of its own",
                    text: "“\(corrected.concept?.name ?? model.text)” is its own concept now. The resolver's call is kept, with your correction beside it.",
                    tint: .bucketClosed, wash: .bucketClosedBG
                )
                Button("Done", action: done)
                    .buttonStyle(UnrotButton(weight: .primary, fill: true))
                    .keyboardShortcut(.defaultAction)
            case .failed(let message):
                Verdict(title: "Not filed", text: message, tint: .bucketOpen, wash: .bucketOpenBG)
                HStack {
                    Button("Try again") { model.reset(selection: model.fromSelection ? model.text : nil, from: model.seenIn) }
                        .buttonStyle(UnrotButton(weight: .primary, fill: true))
                        .keyboardShortcut(.defaultAction)
                    Button("Close", action: done).buttonStyle(UnrotButton()).keyboardShortcut(.cancelAction)
                }
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 30)
        .padding(.bottom, 18)
        .frame(width: 440)
        .background(Color.paper)
    }

    @ViewBuilder
    private var composing: some View {
        VStack(alignment: .leading, spacing: 6) {
            Eyebrow(text: model.fromSelection ? "What you selected" : "The term")
            if model.fromSelection {
                Text("“\(model.text)”")
                    .font(.system(size: 14))
                    .lineLimit(4)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.sunk, in: RoundedRectangle(cornerRadius: 7))
            } else {
                field(TextField("", text: $model.text, prompt: Text("something about backpressure?").foregroundStyle(Color.inkFaint)))
            }
        }
        VStack(alignment: .leading, spacing: 6) {
            Eyebrow(text: "In your own words — optional")
            field(TextField("", text: $model.ownWords, prompt: Text("where you met it, or what you think it means").foregroundStyle(Color.inkFaint), axis: .vertical)
                .lineLimit(2...4))
        }
        if let app = model.seenIn {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Remember it came from \(app)").font(.system(size: 13, weight: .semibold))
                    Text("The app's name. Never the contents.").font(.system(size: 11)).foregroundStyle(Color.inkFaint)
                }
                Spacer()
                Toggle("", isOn: $model.rememberSource).toggleStyle(.switch).tint(Color.watching).labelsHidden()
            }
            .padding(10)
            .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
        }
        HStack(spacing: 8) {
            Button { Task { await model.send() } } label: {
                if model.phase == .sending { ProgressView().controlSize(.small) } else { Text("Add") }
            }
            .buttonStyle(UnrotButton(weight: .primary, fill: true))
            .keyboardShortcut(.defaultAction)
            .disabled(!model.canSend)
            Button("Cancel", action: done)
                .buttonStyle(UnrotButton())
                .keyboardShortcut(.cancelAction)
        }
        Text(model.fromSelection
             ? "unrot receives the selection and nothing else — not the page, not the document."
             : "Tip: select text in any app, then right-click › Services › Add to unrot.")
            .font(.system(size: 11))
            .foregroundStyle(Color.inkFaint)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func field<F: View>(_ content: F) -> some View {
        content
            .textFieldStyle(.plain)
            .font(.system(size: 13.5))
            .padding(10)
            .background(Color.card, in: RoundedRectangle(cornerRadius: 7))
            .overlay(RoundedRectangle(cornerRadius: 7).stroke(Color.ruleStrong, lineWidth: 1))
    }
}

private struct Verdict: View {
    let title: String
    let text: String
    let tint: Color
    let wash: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.system(size: 13, weight: .semibold)).foregroundStyle(tint)
            // Markdown, so the concept name can be bold as the canvas has it.
            Text(LocalizedStringKey(text))
                .font(.system(size: 12.5))
                .foregroundStyle(Color.inkPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(wash, in: RoundedRectangle(cornerRadius: 8))
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
        if submitted.decision == "new" {
            Verdict(
                title: "A new gap",
                text: "**\(submitted.canonicalName)** is waiting beside the ones unrot found. \(submitted.reasoning)",
                tint: .bucketClosed, wash: .bucketClosedBG
            )
        } else {
            Verdict(
                title: "Resolved to an existing concept",
                text: "This looks like **\(submitted.canonicalName)**, which you've met before. \(submitted.reasoning)",
                tint: .bucketLearning, wash: .bucketLearningBG
            )
        }
        if let unavailable = submitted.modelUnavailable {
            RefusalNote(text: unavailable)
        }
        HStack(spacing: 8) {
            if submitted.isArguable {
                Button("That's it", action: done)
                    .buttonStyle(UnrotButton(weight: .primary, fill: true))
                    .keyboardShortcut(.defaultAction)
                Button("No — this is new") { Task { await model.itIsNew() } }
                    .buttonStyle(UnrotButton(fill: true))
            } else {
                Button("Done", action: done)
                    .buttonStyle(UnrotButton(weight: .primary, fill: true))
                    .keyboardShortcut(.defaultAction)
            }
        }
    }
}
