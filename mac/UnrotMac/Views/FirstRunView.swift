//  FirstRunView.swift
//  UnrotMac
//
//  After the FirstRun artboard: three steps -- the folder, where analysis runs,
//  and whether to watch -- before anything is captured.
//
//  The watcher does not start until this is finished. What unrot reads, where
//  its model calls go, and whether it spends money on its own are the three
//  things worth saying before it does any of them.

import Observation
import SwiftUI
import UnrotKit

@MainActor
@Observable
final class Onboarding {
    private static let key = "UnrotOnboarded"
    private(set) var done = UserDefaults.standard.bool(forKey: key)
    var onFinish: () -> Void = {}

    func finish() {
        UserDefaults.standard.set(true, forKey: Self.key)
        done = true
        onFinish()
    }
}

struct FirstRunView: View {
    let onboarding: Onboarding
    @Bindable var watcher: Watcher
    @Bindable var settings: ModelSettings
    @State var step = 1
    @State private var key = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Steps(current: step)
            switch step {
            case 1: folder
            case 2: model
            default: watch
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 44)
        .padding(.vertical, 30)
        .frame(maxWidth: 760, alignment: .leading)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color.paper)
    }

    // MARK: - 1. The folder

    private var folder: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("unrot needs to read one folder.").font(.display(34))
            Text("Claude Code already writes every session to your disk. unrot reads those files and nothing else — it is not a plugin, a hook, or anything that runs inside the agent.")
                .font(.system(size: 14)).foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 12) {
                Image(systemName: "folder").foregroundStyle(Color.inkSoft)
                Text("~/.claude/projects").font(.system(size: 13.5, design: .monospaced))
                Spacer()
                Text(found).font(.system(size: 12)).foregroundStyle(Color.inkFaint)
            }
            .padding(16)
            .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.rule, lineWidth: 1))
            VStack(alignment: .leading, spacing: 10) {
                promise("Read-only.", "Nothing in unrot ever writes into ~/.claude.")
                promise("A copy is kept", "in ~/.unrot/raw so an improved detector can re-read your history later. That directory never syncs and never uploads.")
                promise("Your workflow does not change.", "No hooks, no slash command, no injected text, nothing to notice.")
            }
            HStack {
                Spacer()
                Button("Continue") { step = 2 }
                    .buttonStyle(UnrotButton(weight: .primary))
                    .keyboardShortcut(.defaultAction)
            }
        }
    }

    private var found: String {
        let files = FileManager.default.enumerator(at: ClaudeActivity.projects, includingPropertiesForKeys: nil)
        var count = 0
        while let file = files?.nextObject() as? URL {
            if file.pathExtension == "jsonl" { count += 1 }
        }
        return count == 0 ? "no sessions yet" : "\(count) sessions here"
    }

    private func promise(_ lead: String, _ rest: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: "checkmark.circle").foregroundStyle(Color.bucketClosed)
            (Text(lead).bold() + Text(" \(rest)"))
                .font(.system(size: 13.5))
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - 2. Where analysis runs

    private var model: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Where should analysis run?").font(.display(34))
            Text("Capture needs no model. Finding gaps, grading your answers and writing material do — and you choose where those calls go.")
                .font(.system(size: 14)).foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
            HStack(alignment: .top, spacing: 12) {
                choice(.hosted, "A hosted model", "Transcript windows are sent to OpenRouter, to the model you pick.") {
                    SecureField("", text: $key, prompt: Text(settings.hasKey ? "A key is stored" : "Paste your OpenRouter key").foregroundStyle(Color.inkFaint))
                        .modifier(Field(mono: true))
                }
                choice(.local, "A model on this Mac", "Nothing leaves the machine at all. Slower, and free.") {
                    TextField("", text: $settings.localURL, prompt: Text("http://localhost:11434/v1").foregroundStyle(Color.inkFaint))
                        .modifier(Field(mono: true))
                }
            }
            HStack {
                Button("Back") { step = 1 }.buttonStyle(UnrotButton())
                Spacer()
                Button("Skip for now") { step = 3 }.buttonStyle(LinkButton())
                Button("Continue") {
                    if settings.endpoint == .hosted, !key.isEmpty { settings.saveKey(key) }
                    key = ""
                    step = 3
                }
                .buttonStyle(UnrotButton(weight: .primary))
                .keyboardShortcut(.defaultAction)
            }
        }
    }

    private func choice<F: View>(
        _ endpoint: ModelSettings.Endpoint, _ title: String, _ text: String, @ViewBuilder field: () -> F
    ) -> some View {
        let chosen = settings.endpoint == endpoint
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(title).font(.system(size: 14, weight: .semibold))
                Spacer()
                Image(systemName: chosen ? "largecircle.fill.circle" : "circle")
                    .foregroundStyle(chosen ? Color.inkPrimary : Color.inkFaint)
            }
            Text(text).font(.system(size: 12.5)).foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
            if chosen { field() }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(chosen ? Color.inkPrimary : Color.rule, lineWidth: chosen ? 1.5 : 1))
        .contentShape(Rectangle())
        .onTapGesture { settings.endpoint = endpoint }
    }

    // MARK: - 3. Watch

    private var watch: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Watch for finished sessions?").font(.display(34))
            Text("unrot takes a session once it and its project have been quiet for \(watcher.quietMinutes) minutes — the detector judges what you said after a term was used, and mid-session there is nothing to judge yet.")
                .font(.system(size: 14)).foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
            VStack(spacing: 0) {
                toggleRow("Capture sessions when they finish", "A copy, on this Mac. No model is called and nothing is sent.",
                          isOn: Binding(get: { !watcher.paused }, set: { watcher.paused = !$0 }))
                Divider()
                toggleRow("Analyse them automatically", "Off, captured sessions wait for you to click Analyse now. On, each finished session is sent to your model and costs what that costs.",
                          isOn: $watcher.autoAnalyse)
            }
            .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.rule, lineWidth: 1))
            Text("Both can be changed any time in Settings. Nothing you had before is analysed automatically, whichever you choose.")
                .font(.system(size: 12)).foregroundStyle(Color.inkFaint)
            HStack {
                Button("Back") { step = 2 }.buttonStyle(UnrotButton())
                Spacer()
                Button(watcher.paused ? "Finish" : "Start watching") { onboarding.finish() }
                    .buttonStyle(UnrotButton(weight: .primary))
                    .keyboardShortcut(.defaultAction)
            }
        }
    }

    private func toggleRow(_ title: String, _ text: String, isOn: Binding<Bool>) -> some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 13.5, weight: .semibold))
                Text(text).font(.system(size: 12)).foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            Toggle("", isOn: isOn).toggleStyle(.switch).tint(Color.watching).labelsHidden()
        }
        .padding(16)
    }
}

private struct Steps: View {
    let current: Int
    private let names = ["Folder", "Model", "Watch"]

    var body: some View {
        HStack(spacing: 10) {
            ForEach(Array(names.enumerated()), id: \.offset) { index, name in
                let number = index + 1
                HStack(spacing: 7) {
                    Text("\(number)")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(number == current ? Color.paper : Color.inkSoft)
                        .frame(width: 22, height: 22)
                        .background(Circle().fill(number == current ? Color.inkPrimary : Color.clear))
                        .overlay(Circle().stroke(number == current ? Color.clear : Color.ruleStrong, lineWidth: 1))
                    Text(name)
                        .font(.system(size: 13, weight: number == current ? .semibold : .regular))
                        .foregroundStyle(number == current ? Color.inkPrimary : Color.inkSoft)
                }
                if number < names.count {
                    Rectangle().fill(Color.ruleStrong).frame(width: 28, height: 1)
                }
            }
        }
    }
}
