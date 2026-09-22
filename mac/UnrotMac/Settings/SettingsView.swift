//  SettingsView.swift
//  UnrotMac
//
//  The settings window. Phase 5 adds the capture folder, the model and the key;
//  this is what phases 4 and 4b need.

import ServiceManagement
import SwiftUI
import UnrotKit

struct SettingsView: View {
    let notifier: Notifier
    let watcher: Watcher
    let model: ModelSettings
    let regenerator: Regenerator
    let client: UnrotClient
    let restartCore: () -> Void

    var body: some View {
        TabView {
            WatchingPane(watcher: watcher)
                .tabItem { Label("Watching", systemImage: "eye") }
            ModelPane(settings: model, client: client, restartCore: restartCore)
                .tabItem { Label("Model", systemImage: "cpu") }
            NotificationsPane(notifier: notifier)
                .tabItem { Label("Notifications", systemImage: "bell") }
            CapturePane()
                .tabItem { Label("Capture", systemImage: "text.cursor") }
            RegeneratePane(regenerator: regenerator)
                .tabItem { Label("Regenerate", systemImage: "arrow.triangle.2.circlepath") }
        }
        .frame(width: 560, height: 520)
    }
}

struct ModelPane: View {
    @Bindable var settings: ModelSettings
    let client: UnrotClient
    let restartCore: () -> Void

    @State private var key = ""
    @State private var inEffect: CoreConfig?

    var body: some View {
        Form {
            Section {
                Picker("Endpoint", selection: $settings.endpoint) {
                    ForEach(ModelSettings.Endpoint.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.radioGroup)
                switch settings.endpoint {
                case .hosted:
                    EmptyView()
                case .local:
                    TextField("Server URL", text: $settings.localURL)
                case .custom:
                    TextField("Server URL", text: $settings.customURL, prompt: Text("https://…/v1"))
                }
                TextField("Model", text: $settings.model, prompt: Text("the core's default"))
            } footer: {
                if settings.endpoint == .local {
                    Text("Any OpenAI-compatible server on this Mac — Ollama, LM Studio, llama.cpp. Nothing leaves the machine.")
                        .font(.system(size: 11)).foregroundStyle(Color.inkFaint)
                }
            }

            if settings.endpoint != .local {
                Section("API key") {
                    LabeledContent("Status") {
                        Text(settings.hasKey ? "Stored in your login Keychain" : "Not set")
                            .foregroundStyle(settings.hasKey ? Color.inkSoft : Color.inkFaint)
                    }
                    HStack {
                        SecureField("Paste a key", text: $key)
                        Button("Save") { settings.saveKey(key); key = "" }
                            .disabled(key.trimmingCharacters(in: .whitespaces).isEmpty)
                        if settings.hasKey {
                            Button("Remove", role: .destructive) { settings.removeKey() }
                        }
                    }
                }
            }

            Section {
                if let inEffect {
                    LabeledContent("Model", value: inEffect.model)
                    LabeledContent("Endpoint", value: inEffect.baseUrl)
                    LabeledContent("Key", value: inEffect.keySet ? "set" : "not set")
                    LabeledContent("Grading", value: inEffect.grader == "classifier" ? "by model" : "keyword only — no model")
                } else {
                    Text("The core isn't answering.").foregroundStyle(Color.inkFaint)
                }
                if settings.changed {
                    HStack {
                        Text("Changes apply when the core restarts.")
                            .font(.system(size: 11)).foregroundStyle(Color.inkFaint)
                        Spacer()
                        Button("Restart the Core") {
                            restartCore()
                            settings.applied()
                            Task {
                                try? await Task.sleep(for: .seconds(3))
                                inEffect = try? await client.config()
                            }
                        }
                        .buttonStyle(.borderedProminent)
                    }
                }
            } header: {
                Text("In effect")
            } footer: {
                Text("Read back from the core, because an environment variable set outside the app wins over these settings — the same order `python -m unrot.resolver env` shows.")
                    .font(.system(size: 11)).foregroundStyle(Color.inkFaint)
            }

            Section("What leaves this Mac") {
                if let inEffect {
                    if inEffect.leavesThisMac.isEmpty {
                        Text("Nothing. Every model call goes to this Mac.")
                            .font(.system(size: 12))
                    } else {
                        ForEach(inEffect.leavesThisMac, id: \.self) { line in
                            Text(line).font(.system(size: 12)).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    Text("Raw transcripts never leave: everything under ~/.unrot/raw stays on this machine.")
                        .font(.system(size: 11)).foregroundStyle(Color.inkFaint)
                }
            }
        }
        .formStyle(.grouped)
        .task { inEffect = try? await client.config() }
    }
}

struct RegeneratePane: View {
    let regenerator: Regenerator
    @State private var confirming = false

    var body: some View {
        Form {
            Section {
                Text("Re-run the detector over everything captured, under the model and prompt in effect now. Worth doing after either changes.")
                    .font(.system(size: 12))
                    .fixedSize(horizontal: false, vertical: true)
                if let plan = regenerator.plan {
                    LabeledContent("Sessions captured", value: "\(plan.captured)")
                    LabeledContent("Already at this version", value: "\(plan.alreadyDone)")
                    LabeledContent("Would be re-examined", value: "\(plan.toRun.count)")
                    Text(plan.detectorVersion)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(Color.inkFaint)
                        .textSelection(.enabled)
                } else if let error = regenerator.lastError {
                    Text(error).foregroundStyle(Color.inkSoft)
                }
            }

            Section {
                // The sentence that matters, on screen rather than in a manual.
                Label {
                    Text(protectedLine).fixedSize(horizontal: false, vertical: true)
                } icon: {
                    Image(systemName: "lock")
                }
                .font(.system(size: 12))
            }

            Section {
                if let progress = regenerator.progress {
                    ProgressView(value: Double(progress.done), total: Double(max(progress.total, 1))) {
                        Text("Re-examined \(progress.done) of \(progress.total)")
                    }
                    Button("Stop") { regenerator.stop() }
                } else {
                    let n = regenerator.plan?.toRun.count ?? 0
                    Button("Regenerate \(n) session\(n == 1 ? "" : "s")…") { confirming = true }
                        .disabled(n == 0 || regenerator.plan?.canRun != true)
                    if regenerator.plan?.canRun == false {
                        Text("No model is configured, so nothing can be re-examined.")
                            .font(.system(size: 11)).foregroundStyle(Color.inkFaint)
                    }
                }
                if let error = regenerator.lastError, regenerator.plan != nil {
                    Text(error).font(.system(size: 11)).foregroundStyle(Color.inkSoft)
                }
                if regenerator.totals.removed + regenerator.totals.recorded > 0 {
                    Text("Last run: \(regenerator.totals.recorded) recorded, \(regenerator.totals.removed) stale flags removed, \(regenerator.totals.protected) judged encounters left alone.")
                        .font(.system(size: 11)).foregroundStyle(Color.inkFaint)
                }
            }
        }
        .formStyle(.grouped)
        .task { await regenerator.refresh() }
        .confirmationDialog(
            "Regenerate \(regenerator.plan?.toRun.count ?? 0) sessions?",
            isPresented: $confirming
        ) {
            Button("Regenerate") { regenerator.start() }
        } message: {
            Text("This makes model calls for each session, and costs what that costs. It can be stopped between any two sessions, and your judgments are not touched.")
        }
    }

    private var protectedLine: String {
        let n = regenerator.plan?.protected ?? 0
        let count = n == 0 ? "" : " \(n) encounter\(n == 1 ? "" : "s") carry your judgment."
        return "Your judgments are protected and replayed untouched.\(count) Anything you confirmed, dismissed or explained is left exactly as it is; only the machine's own flags are re-examined."
    }
}

struct WatchingPane: View {
    @Bindable var watcher: Watcher

    var body: some View {
        Form {
            Section {
                OpenAtLogin()
                Toggle("Capture sessions when they finish", isOn: Binding(
                    get: { !watcher.paused },
                    set: { watcher.paused = !$0 }
                ))
                Stepper(value: $watcher.quietMinutes, in: 2...60) {
                    Text("Wait \(watcher.quietMinutes) minutes after a session goes quiet")
                }
                .disabled(watcher.paused)
            } footer: {
                Text("""
                    Capture copies a transcript Claude Code already wrote to disk into \
                    ~/.unrot/raw, which never syncs. No model is called. A session is only \
                    taken once it and everything else in its project have been quiet this \
                    long — the detector judges your next turn, and mid-session there isn't one.
                    """)
                .font(.system(size: 11))
                .foregroundStyle(Color.inkFaint)
            }

            Section {
                Toggle("Analyse finished sessions automatically", isOn: $watcher.autoAnalyse)
                    .disabled(watcher.paused)
            } footer: {
                Text("""
                    Analysis sends a session's turns to your model endpoint, one call per \
                    stretch of conversation, and costs what that costs. Off, sessions are \
                    captured and wait for Analyse now. On, it applies only to sessions that \
                    finish after you switch it on — anything already waiting still waits \
                    for you.
                    """)
                .font(.system(size: 11))
                .foregroundStyle(Color.inkFaint)
            }

            Section("Waiting to be analysed") {
                if let pending = watcher.queue?.pending, !pending.isEmpty {
                    ForEach(pending.prefix(8)) { session in
                        LabeledContent(session.repo ?? session.sessionId) {
                            Text("\(session.humanTurns) turns\(session.reason == "grown" ? ", continued" : "")")
                                .foregroundStyle(Color.inkFaint)
                        }
                        .font(.system(size: 12))
                    }
                    if pending.count > 8 {
                        Text("and \(pending.count - 8) more")
                            .font(.system(size: 11))
                            .foregroundStyle(Color.inkFaint)
                    }
                    HStack {
                        if let error = watcher.lastError {
                            Text(error).font(.system(size: 11)).foregroundStyle(Color.inkSoft)
                        }
                        Spacer()
                        if watcher.isRunning {
                            Button("Stop") { watcher.stopAnalysing() }
                        } else {
                            Button("Analyse \(pending.count) now") { watcher.analyseNow() }
                                .disabled(!watcher.canAnalyse)
                        }
                    }
                } else {
                    Text("Nothing waiting.")
                        .font(.system(size: 12))
                        .foregroundStyle(Color.inkFaint)
                }
            }
        }
        .formStyle(.grouped)
        .task { await watcher.refreshQueue() }
    }
}

struct NotificationsPane: View {
    @Bindable var notifier: Notifier

    var body: some View {
        Form {
            Section {
                Toggle("Send one quiet notification a day", isOn: Binding(
                    get: { notifier.enabled },
                    set: { on in Task { await notifier.setEnabled(on) } }
                ))
                Picker("Between", selection: $notifier.hour) {
                    ForEach(6..<23, id: \.self) { hour in
                        Text(Self.window(hour)).tag(hour)
                    }
                }
                .disabled(!notifier.enabled)
                Toggle("Ask the comprehension check in the notification", isOn: $notifier.carriesCheck)
                    .disabled(!notifier.enabled)
            } footer: {
                VStack(alignment: .leading, spacing: 6) {
                    if notifier.permissionDenied {
                        Text("macOS has notifications for unrot switched off. Turn them on in System Settings › Notifications, then try again.")
                            .foregroundStyle(Color.alarm)
                    }
                    Text("""
                        Nothing is sent when a gap is found. At most one notification a day, \
                        in the hour you choose — never on a day with nothing waiting, never \
                        while a Claude Code session is live, and never twice about the same \
                        gap. Never for material, grades or progress. No sound, no badge.
                        """)
                    Text("With the check switched on, a single gap asks its question and takes a one- or two-line answer. It is never the only way to answer it.")
                }
                .font(.system(size: 11))
                .foregroundStyle(Color.inkFaint)
            }
        }
        .formStyle(.grouped)
    }

    private static func window(_ hour: Int) -> String {
        func label(_ h: Int) -> String {
            let twelve = h % 12 == 0 ? 12 : h % 12
            return "\(twelve) \(h < 12 ? "am" : "pm")"
        }
        return "\(label(hour)) and \(label(hour + 1))"
    }
}

struct CapturePane: View {
    @State private var retained: String?

    var body: some View {
        Form {
            Section {
                LabeledContent("Retained copies") {
                    HStack {
                        Text(retained ?? "…").foregroundStyle(Color.inkSoft)
                        Button("Reveal in Finder") {
                            NSWorkspace.shared.activateFileViewerSelecting([Self.raw])
                        }
                    }
                }
            } footer: {
                Text("Everything under ~/.unrot/raw never syncs, never uploads, and never leaves this Mac. unrot keeps its own copy of each transcript so a moment can still be replayed after Claude Code rotates the original away.")
                    .font(.system(size: 11)).foregroundStyle(Color.inkFaint)
            }

            Section {
                LabeledContent("From any app") {
                    Text("Select text › right-click › Services › Add to unrot")
                        .font(.system(size: 12))
                }
                LabeledContent("Keyboard shortcut") {
                    Button("Set in System Settings…") {
                        if let url = URL(string: "x-apple.systempreferences:com.apple.Keyboard-Settings.extension") {
                            NSWorkspace.shared.open(url)
                        }
                    }
                }
            } footer: {
                Text("""
                    macOS lists third-party items under Services rather than at the top of \
                    the right-click menu. The shortcut belongs to the service, so it is set \
                    under Keyboard Shortcuts › Services — ⌥⌘U is the suggested one. That \
                    way unrot never needs Accessibility permission, and receives only the \
                    text you selected.
                    """)
                .font(.system(size: 11))
                .foregroundStyle(Color.inkFaint)
            }
        }
        .formStyle(.grouped)
        .task { retained = await Self.size() }
    }

    nonisolated static var raw: URL {
        FileManager.default.homeDirectoryForCurrentUser.appending(path: ".unrot/raw")
    }

    /// Measured off the main thread: a year of transcripts is a lot of files.
    static func size() async -> String {
        await Task.detached(priority: .utility) { measure() }.value
    }

    nonisolated private static func measure() -> String {
        var total: Int64 = 0
        if let files = FileManager.default.enumerator(at: raw, includingPropertiesForKeys: [.fileSizeKey]) {
            for case let url as URL in files {
                total += Int64((try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
            }
        }
        return ByteCountFormatter.string(fromByteCount: total, countStyle: .file)
    }
}

/// The watcher only watches while the app is running, so starting with the Mac
/// is part of watching rather than a convenience. `SMAppService` rather than a
/// hand-written LaunchAgent plist: the system owns the registration, lists it
/// in System Settings › Login Items where the user can see and revoke it, and
/// there is no file of ours left behind in ~/Library if the app is deleted.
private struct OpenAtLogin: View {
    @State private var enabled = SMAppService.mainApp.status == .enabled
    @State private var problem: String?

    var body: some View {
        Toggle("Open unrot at login", isOn: Binding(
            get: { enabled },
            set: { on in
                do {
                    if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
                    problem = nil
                } catch {
                    problem = error.localizedDescription
                }
                enabled = SMAppService.mainApp.status == .enabled
            }
        ))
        if let problem {
            Text(problem).font(.system(size: 11)).foregroundStyle(Color.inkSoft)
        }
    }
}
