//  SettingsView.swift
//  UnrotMac
//
//  After the Capture and Model artboards: settings as the Mac lays them out --
//  a pane per toolbar item, each a grouped form -- with the privacy story told
//  right under the choice that changes it.

import ServiceManagement
import SwiftUI
import UniformTypeIdentifiers
import UnrotKit

struct SettingsView: View {
    let notifier: Notifier
    let watcher: Watcher
    let model: ModelSettings
    let regenerator: Regenerator
    let client: UnrotClient
    let updater: Updater
    let menuBar: MenuBarPresence
    let restartCore: () -> Void

    enum Tab: String { case capture, model, notifications, advanced, developer }
    /// Reopens on the pane last used: people adjust related settings more than
    /// once. Also how the main window opens Settings on Advanced (see
    /// ReexamineBanner) -- it writes this, then opens Settings.
    @AppStorage(SettingsView.tabKey) private var tab: Tab = .capture
    static let tabKey = "settings.tab"

    var body: some View {
        // Each pane sets its own height, and the window follows it: a pane of
        // three toggles is not padded out to the size of the longest one.
        TabView(selection: $tab) {
            CapturePane(watcher: watcher, menuBar: menuBar)
                .frame(width: Self.width, height: 600)
                .tabItem { Label("Capture", systemImage: "tray.and.arrow.down") }.tag(Tab.capture)
            ModelPane(settings: model, client: client, restartCore: restartCore)
                .frame(width: Self.width, height: 640)
                .tabItem { Label("Model", systemImage: "cpu") }.tag(Tab.model)
            NotificationsPane(notifier: notifier)
                .frame(width: Self.width, height: 320)
                .tabItem { Label("Notifications", systemImage: "bell.badge") }.tag(Tab.notifications)
            RegeneratePane(regenerator: regenerator, updater: updater)
                .frame(width: Self.width, height: 600)
                .tabItem { Label("Advanced", systemImage: "gearshape.2") }.tag(Tab.advanced)
            #if DEV_FEATURES
            DeveloperPane(client: client, regenerator: regenerator, restartCore: restartCore)
                .frame(width: Self.width, height: 620)
                .tabItem { Label("Developer", systemImage: "hammer") }.tag(Tab.developer)
            #endif
        }
    }

    static let width: CGFloat = 680
}

struct Field: ViewModifier {
    var mono = false
    func body(content: Content) -> some View {
        content
            .textFieldStyle(.plain)
            .font(.system(.body, design: mono ? .monospaced : .default))
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(Color.card, in: RoundedRectangle(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.ruleStrong, lineWidth: 1))
    }
}

/// A section's explanation, as grouped forms set it: under the rows, smaller,
/// secondary.
private struct Caption: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(.system(.subheadline))
            .foregroundStyle(Color.inkFaint)
            .fixedSize(horizontal: false, vertical: true)
    }
}

// MARK: - Capture

struct CapturePane: View {
    @Bindable var watcher: Watcher
    /// Nil where there is no menu-bar item to show or hide (snapshots).
    var menuBar: MenuBarPresence? = nil
    @State private var retained: String?

    var body: some View {
        Form {
            Section {
                LabeledContent("Transcript folder") {
                    Text("~/.claude/projects").font(.system(.callout, design: .monospaced))
                }
            } footer: {
                Caption("Read-only, always. unrot lists and reads these files; nothing in it ever writes to ~/.claude.")
            }

            Section {
                Toggle("Watch for new sessions", isOn: Binding(get: { !watcher.paused }, set: { watcher.paused = !$0 }))
                LabeledContent("Take a session once quiet for") {
                    HStack(spacing: 6) {
                        TextField("Minutes", value: $watcher.quietMinutes, format: .number)
                            .labelsHidden()
                            .multilineTextAlignment(.trailing)
                            .frame(width: 44)
                        Stepper("Minutes", value: $watcher.quietMinutes, in: 2...60).labelsHidden()
                        Text("minutes")
                    }
                }
                Label("Never takes a project while Claude Code is still running in it", systemImage: "checkmark.circle")
                    .foregroundStyle(Color.inkSoft)
            } header: {
                Text("Watching")
            } footer: {
                Caption("FSEvents, not polling: it runs whether or not the window is open, and costs nothing while you work. A session is taken once it goes quiet, because the detector judges what you said after a term was used -- while a session is live that turn does not exist yet, and re-reading a growing transcript costs tokens every time.")
            }

            Section {
                Toggle("Analyse automatically", isOn: $watcher.autoAnalyse)
                    .disabled(watcher.paused)
                QueueRow(watcher: watcher)
            } header: {
                Text("Analysis")
            } footer: {
                Caption("Analysis sends a session's windows to your model and costs what that costs. Off, captured sessions wait for Analyse now. On, it applies only to sessions that finish after you switch it on — anything already waiting still waits for you.")
            }

            Section {
                LabeledContent("Retained copies") {
                    HStack(spacing: 10) {
                        Text("\(retained ?? "…") in ~/.unrot/raw")
                        Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([Self.raw]) }
                    }
                }
            } footer: {
                Caption("Kept so the moment view can replay what was said, and so a better detector can re-read your history. Everything under raw/ never syncs, never uploads, never leaves this Mac: the sync boundary is a path prefix, not a setting you have to trust.")
            }

            Section {
                LabeledContent("From any app") {
                    HStack(spacing: 10) {
                        Text("Services › Add to unrot")
                        Button("Set a shortcut…") {
                            if let url = URL(string: "x-apple.systempreferences:com.apple.Keyboard-Settings.extension") {
                                NSWorkspace.shared.open(url)
                            }
                        }
                    }
                }
            } footer: {
                Caption("Select text, right-click, and choose Services › Add to unrot. macOS lists third-party items under Services, not at the top of the menu. Its shortcut belongs to the service — ⌥⌘U is the suggested one — so unrot never needs Accessibility permission.")
            }

            Section {
                OpenAtLogin()
                if let menuBar {
                    Toggle("Show in menu bar", isOn: Bindable(menuBar).isVisible)
                }
            } footer: {
                Caption("Watching only happens while unrot is running, so opening at login is part of watching. The menu-bar item shows what is waiting and answers the top gap; the Dock menu and the window do everything it does.")
            }
        }
        .formStyle(.grouped)
        .task {
            retained = await Self.size()
            await watcher.refreshQueue()
        }
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

/// The queue, as a row of the Analysis section: what is waiting, and the
/// buttons that act on it.
private struct QueueRow: View {
    let watcher: Watcher

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            LabeledContent("Queue") {
                HStack(spacing: 8) {
                    Text(summary)
                    if watcher.isRunning {
                        Button("Stop") { watcher.stopAnalysing() }
                    } else {
                        Button(watcher.paused ? "Resume" : "Pause") { watcher.paused.toggle() }
                        Button("Analyse now") { watcher.analyseNow() }
                            .disabled(!watcher.canAnalyse || watcher.pendingCount == 0)
                    }
                }
            }
            if let progress = watcher.progress {
                ProgressView(value: Double(progress.done), total: Double(max(progress.total, 1)))
            }
            if let pending = watcher.queue?.pending, !pending.isEmpty {
                Text(pending.prefix(3).map { "\($0.repo ?? $0.sessionId.prefix(8).description) · \($0.humanTurns) turns" }.joined(separator: "   "))
                    .font(.system(.subheadline, design: .monospaced))
                    .foregroundStyle(Color.inkFaint)
                    .lineLimit(1)
            }
            if !watcher.canAnalyse && watcher.pendingCount > 0 {
                Caption("No model is configured, so these can't be analysed yet. Add one under Model.")
            }
            if let error = watcher.lastError {
                Caption(error)
            }
        }
    }

    private var summary: String {
        let n = watcher.pendingCount
        if let progress = watcher.progress {
            let spent = watcher.batchSpent.flatMap { $0.calls > 0 ? " · \($0.text) so far" : nil } ?? ""
            return "Analysing \(progress.done + 1) of \(progress.total)" + spent
        }
        if n == 0 { return "Nothing waiting" }
        let estimate = watcher.canAnalyse ? (watcher.estimate?.text.map { " · \($0)" } ?? "") : ""
        return "\(n) session\(n == 1 ? "" : "s") waiting" + estimate + (watcher.paused ? " · paused" : "")
    }
}

// MARK: - Model

struct ModelPane: View {
    @Bindable var settings: ModelSettings
    let client: UnrotClient
    let restartCore: () -> Void

    @State private var key = ""
    @State private var inEffect: CoreConfig?
    @State private var spend: Spend?

    var body: some View {
        VStack(spacing: 0) {
            Form {
                Section {
                    Picker("Where analysis runs", selection: $settings.endpoint) {
                        Text("OpenRouter").tag(ModelSettings.Endpoint.hosted)
                        Text("On this Mac").tag(ModelSettings.Endpoint.local)
                        Text("Custom endpoint").tag(ModelSettings.Endpoint.custom)
                    }
                    .pickerStyle(.segmented)
                    if settings.endpoint == .local {
                        LabeledContent("Server") {
                            TextField("Server", text: $settings.localURL, prompt: Text("http://localhost:11434/v1"))
                                .labelsHidden().font(.system(.callout, design: .monospaced))
                        }
                    } else if settings.endpoint == .custom {
                        LabeledContent("Endpoint") {
                            TextField("Endpoint", text: $settings.customURL, prompt: Text("https://…/v1"))
                                .labelsHidden().font(.system(.callout, design: .monospaced))
                        }
                    }
                } footer: {
                    if settings.endpoint == .local {
                        Caption("Any OpenAI-compatible server on this Mac — Ollama, LM Studio, llama.cpp.")
                    }
                }

                // Right under the choice that changes it.
                Section {
                    leaves
                } header: {
                    Text("What leaves this Mac, right now")
                } footer: {
                    Caption("Switch to On this Mac and the first list empties. That is the whole privacy story — one setting, not a rewrite.")
                }

                Section {
                    // The value is an identifier, so it is set in mono; the label is not.
                    LabeledContent("Model") {
                        TextField("Model", text: $settings.model, prompt: Text(inEffect.map { "default: \($0.model)" } ?? "the core's default"))
                            .labelsHidden().font(.system(.callout, design: .monospaced))
                    }
                    if settings.endpoint != .local {
                        Picker("Reasoning effort", selection: $settings.effort) {
                            ForEach(ModelSettings.Effort.allCases) { Text($0.label).tag($0) }
                        }
                        .pickerStyle(.segmented)
                    }
                } footer: {
                    Caption("The model is used by the detector, the resolver and material. The detector reads transcript windows, so this line is where the bill actually lives; leave it empty for the core's default. Reasoning effort is how long a reasoning model thinks before it answers: more can find subtler gaps, and is slower, costs more, and on a long session can run out of room before answering. Models that do not reason ignore it.")
                }

                if settings.endpoint != .local {
                    Section {
                        LabeledContent("Sessions at once") {
                            Stepper(value: $settings.atOnce, in: ModelSettings.atOnceRange) {
                                Text("\(settings.atOnce)").font(.system(.body, design: .monospaced))
                            }
                        }
                    } footer: {
                        Caption("How many sessions are examined side by side, and how many parts of one long session are sent together. Each call is mostly waiting on the model, so four at once clears a backlog about four times sooner — the same calls, at the same cost. A server on this Mac always gets one at a time.")
                    }

                    Section {
                        LabeledContent("API key") {
                            HStack(spacing: 8) {
                                SecureField("API key", text: $key, prompt: Text(settings.hasKey ? "A key is stored — paste to replace it" : "Paste your OpenRouter key"))
                                    .labelsHidden()
                                    .font(.system(.callout, design: .monospaced))
                                Button("Save") { settings.saveKey(key); key = "" }
                                    .disabled(key.trimmingCharacters(in: .whitespaces).isEmpty)
                                if settings.hasKey {
                                    Button("Remove") { settings.removeKey() }
                                }
                            }
                        }
                    } footer: {
                        Caption("Kept in the login Keychain, not in a .env beside your code. A key set in your shell still wins.")
                    }
                }

                Section {
                    LabeledContent("Grader") {
                        if let inEffect {
                            // "configured", not "reachable": nothing here probes it,
                            // and a status should not claim a check that never ran.
                            Pip(text: inEffect.grader == "classifier" ? "configured" : "keyword only",
                                tint: inEffect.grader == "classifier" ? .bucketClosed : .bucketOpen,
                                wash: inEffect.grader == "classifier" ? .bucketClosedBG : .bucketOpenBG)
                        }
                    }
                } footer: {
                    Caption("A classifier, separate from the model above. It returns a distribution, which is what keeps the listed/causal line movable later.")
                }

                Section("What analysis has cost") {
                    SpendCard(spend: spend)
                }
            }
            .formStyle(.grouped)

            if settings.changed {
                RestartBar {
                    restartCore()
                    settings.applied()
                    Task { try? await Task.sleep(for: .seconds(3)); inEffect = try? await client.config() }
                }
            }
        }
        .task {
            inEffect = try? await client.config()
            spend = try? await client.spend()
        }
    }

    /// What leaves, told under the choice that changes it. The list comes from
    /// the core -- it lives next to the code that makes the calls.
    @ViewBuilder
    private var leaves: some View {
        if let inEffect {
            if inEffect.leavesThisMac.isEmpty {
                Label("Nothing — every call stays on this Mac", systemImage: "lock")
                    .foregroundStyle(Color.bucketClosed)
            } else {
                Eyebrow(text: "Sent to \(host(inEffect.baseUrl))", tint: .bucketOpen)
                ForEach(inEffect.leavesThisMac, id: \.self) { line in
                    Label { Text(line).fixedSize(horizontal: false, vertical: true) } icon: {
                        Image(systemName: "arrow.up.right").foregroundStyle(Color.bucketOpen)
                    }
                }
            }
        } else {
            Text("The core isn't answering.").foregroundStyle(Color.inkFaint)
        }
        Eyebrow(text: "Never sent, on any setting", tint: .bucketClosed)
        ForEach(["The retained transcript archive under raw/.",
                 "Your confirmations, dismissals and answers, as a record.",
                 "The event log itself, or anything compiled from it."], id: \.self) { line in
            Label { Text(line).fixedSize(horizontal: false, vertical: true) } icon: {
                Image(systemName: "lock").foregroundStyle(Color.bucketClosed)
            }
        }
    }

    private func host(_ url: String) -> String {
        let name = URL(string: url)?.host() ?? url
        return name.contains("openrouter") ? "OpenRouter" : name
    }
}

/// Under the form, while a model change is waiting for the core to restart.
private struct RestartBar: View {
    let restart: () -> Void

    var body: some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Changes apply when the core restarts.").font(.system(.body, weight: .semibold))
                Caption("The core reads its model settings when it starts.")
            }
            Spacer()
            Button("Restart the core", action: restart)
                .buttonStyle(UnrotButton(weight: .primary))
                .keyboardShortcut(.defaultAction)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(Color.bucketLearningBG)
    }
}

struct RegeneratePane: View {
    let regenerator: Regenerator
    let updater: Updater
    @State private var confirming = false

    var body: some View {
        Form {
            Section {
                Text("Re-run the detector over everything captured, under the model and prompt in effect now. Worth doing after either changes.")
                    .font(.system(.callout))
                    .fixedSize(horizontal: false, vertical: true)
                if let plan = regenerator.plan {
                    LabeledContent("Sessions captured", value: "\(plan.captured)")
                    LabeledContent("Already at this version", value: "\(plan.alreadyDone)")
                    LabeledContent("Would be re-examined", value: "\(plan.toRun.count)")
                    Text(plan.detectorVersion)
                        .font(.system(.caption, design: .monospaced))
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
                .font(.system(.callout))
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
                            .font(.system(.subheadline)).foregroundStyle(Color.inkFaint)
                    }
                }
                if let error = regenerator.lastError, regenerator.plan != nil {
                    Text(error).font(.system(.subheadline)).foregroundStyle(Color.inkSoft)
                }
                if regenerator.totals.removed + regenerator.totals.recorded > 0 {
                    Text("Last run: \(regenerator.totals.recorded) recorded, \(regenerator.totals.removed) stale flags removed, \(regenerator.totals.protected) judged encounters left alone.")
                        .font(.system(.subheadline)).foregroundStyle(Color.inkFaint)
                }
            }

            UpdatesSection(updater: updater)
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

// MARK: - Developer

#if DEV_FEATURES
/// Dev-channel builds only, like the rest of the tab.
///
/// Export for analysis (PR-32): everything the models decided, and what you
/// said back, as JSONL in one `.zip`. The core builds the bytes and hands them
/// back; the app writes them only where the save panel says. What goes in and
/// what stays out is the core's own wording, shown before anything is written.
struct DeveloperPane: View {
    let client: UnrotClient
    let regenerator: Regenerator
    let restartCore: () -> Void
    @State private var includeText = false
    @State private var preview: ExportPreview?
    @State private var problem: String?
    @State private var exporting = false
    @State private var saved: URL?

    var body: some View {
        Form {
            BuildInfoSection()
            PipelineSection(client: client)
            LangSmithSection(restartCore: restartCore)
            WipeAndRerunSection(client: client, regenerator: regenerator)
            Section {
                Text("Export for analysis writes the log and every decision the models made, with your verdicts, as JSONL files in one .zip — for an agent or a notebook to judge the models with. It is saved on this Mac, where you choose. Nothing is uploaded.")
                    .font(.system(.callout))
                    .fixedSize(horizontal: false, vertical: true)
                Toggle("Include text", isOn: $includeText)
                Text(includeText
                     ? "Your explanations, the questions they answered, the grader's comments, material text and excerpts go in. Read it before you share it."
                     : "Off: what you wrote, and excerpts from your code and sessions, are left out.")
                    .font(.system(.subheadline))
                    .foregroundStyle(includeText ? Color.inkSoft : Color.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let preview {
                Section("What goes in") {
                    ForEach(preview.included, id: \.self) { line in
                        Label(line, systemImage: "checkmark").font(.system(.callout))
                    }
                    ForEach(preview.files, id: \.name) { file in
                        LabeledContent(file.name, value: "\(file.rows) row\(file.rows == 1 ? "" : "s")")
                            .font(.system(.callout, design: .monospaced))
                    }
                    if preview.fixtureEvents > 0 {
                        Text("\(preview.fixtureEvents) development fixture event\(preview.fixtureEvents == 1 ? "" : "s") included, each marked fixture: true.")
                            .font(.system(.subheadline)).foregroundStyle(Color.inkFaint)
                    }
                }
                Section("What stays out") {
                    ForEach(preview.withheld, id: \.self) { line in
                        Label(line, systemImage: "minus.circle").font(.system(.callout))
                    }
                    ForEach(preview.never, id: \.self) { line in
                        Label(line, systemImage: "lock").font(.system(.callout))
                    }
                }
            } else if let problem {
                Section { Text(problem).foregroundStyle(Color.inkSoft) }
            }

            Section {
                HStack(spacing: 10) {
                    Button(exporting ? "Exporting…" : "Export for analysis…") { choose() }
                        .disabled(exporting || preview == nil)
                    if let saved {
                        Button("Reveal in Finder") { NSWorkspace.shared.activateFileViewerSelecting([saved]) }
                    }
                }
                if let saved {
                    Text("Saved \(saved.lastPathComponent)")
                        .font(.system(.subheadline)).foregroundStyle(Color.inkFaint)
                }
                if let problem, preview != nil {
                    Text(problem).font(.system(.subheadline)).foregroundStyle(Color.inkSoft)
                }
            }
        }
        .formStyle(.grouped)
        .task(id: includeText) { await refresh() }
    }

    private func refresh() async {
        do {
            preview = try await client.exportPreview(includeText: includeText)
            problem = nil
        } catch let error as APIError {
            preview = nil
            problem = error.message
        } catch {
            preview = nil
            problem = "Could not read what an export would contain."
        }
    }

    private func choose() {
        guard let preview else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.zip]
        panel.nameFieldStringValue = preview.filename
        panel.canCreateDirectories = true
        panel.message = "Saved only here. Nothing is uploaded."
        guard panel.runModal() == .OK, let url = panel.url else { return }

        let withText = includeText
        exporting = true
        problem = nil
        saved = nil
        Task {
            do {
                let data = try await client.exportBundle(includeText: withText)
                try data.write(to: url, options: .atomic)
                saved = url
            } catch let error as APIError {
                problem = error.message
            } catch {
                problem = "Could not save the export: \(error.localizedDescription)"
            }
            exporting = false
        }
    }
}
#endif

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
                .font(.system(.subheadline))
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
            Text(problem).font(.system(.subheadline)).foregroundStyle(Color.inkSoft)
        }
    }
}

// MARK: - Spend

/// What analysis has cost, split by what it was spent on, and what one
/// examined session costs with each model -- the number for judging a model
/// choice. Every figure and every "local, no cost" is the core's, from the
/// same functions `python -m unrot.store metrics` prints.
private struct SpendCard: View {
    let spend: Spend?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let spend {
                HStack(alignment: .top, spacing: 28) {
                    column("This week", total: spend.week, parts: spend.weekByPurpose)
                    column("Since install", total: spend.allTime, parts: spend.allTimeByPurpose)
                }
                if !spend.perSession.isEmpty {
                    Divider()
                    Eyebrow(text: "Per examined session", tint: .inkSoft)
                    ForEach(spend.perSession) { row in
                        HStack(spacing: 10) {
                            Text(row.model).font(.system(.callout, design: .monospaced))
                            if row.model == spend.model {
                                Pip(text: "current", tint: .bucketClosed, wash: .bucketClosedBG)
                            }
                            Spacer()
                            Text(row.text).font(.system(.callout, weight: .semibold)).monospacedDigit()
                            Text("over \(row.sessions) session\(row.sessions == 1 ? "" : "s")")
                                .font(.system(.subheadline)).foregroundStyle(Color.inkFaint)
                        }
                    }
                }
                Text(footnote(spend))
                    .font(.system(.subheadline)).foregroundStyle(Color.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("The core isn't answering.").font(.system(.callout)).foregroundStyle(Color.inkFaint)
            }
        }
        .padding(.vertical, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func column(_ title: String, total: SpendTotal, parts: [SpendPart]) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.system(.subheadline)).foregroundStyle(Color.inkSoft)
            Text(total.text).font(.system(.title, weight: .semibold)).monospacedDigit()
            Text("\(total.calls) call\(total.calls == 1 ? "" : "s")"
                 + (total.failed > 0 ? ", \(total.failed) failed" : ""))
                .font(.system(.subheadline)).foregroundStyle(Color.inkFaint)
            ForEach(parts) { part in
                HStack {
                    Text(part.label).font(.system(.callout))
                    Spacer(minLength: 12)
                    Text(part.total.text).font(.system(.callout)).monospacedDigit()
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func footnote(_ spend: Spend) -> String {
        var text = "From the cost OpenRouter reports on every call, failed calls included — a call that runs out of room is billed and produces nothing."
        if spend.allTime.unpriced > 0 {
            text += " \(spend.allTime.unpriced) call\(spend.allTime.unpriced == 1 ? "" : "s") reported no price and \(spend.allTime.unpriced == 1 ? "is" : "are") not in the total."
        }
        if spend.local {
            text += " The model in effect is on this Mac, so analysis has no cost."
        }
        return text
    }
}
