//  SettingsView.swift
//  UnrotMac
//
//  After the Capture and Model artboards: settings as a form with labels on the
//  left, and the privacy story told beside the choice that changes it.

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

    enum Tab: Hashable { case capture, model, notifications, advanced }
    @State private var tab: Tab = .capture

    var body: some View {
        TabView(selection: $tab) {
            CapturePane(watcher: watcher)
                .tabItem { Text("Capture") }.tag(Tab.capture)
            ModelPane(settings: model, client: client, watcher: watcher, regenerator: regenerator,
                      restartCore: restartCore, showPlan: { tab = .advanced })
                .tabItem { Text("Model") }.tag(Tab.model)
            NotificationsPane(notifier: notifier)
                .tabItem { Text("Notifications") }.tag(Tab.notifications)
            RegeneratePane(regenerator: regenerator)
                .tabItem { Text("Advanced") }.tag(Tab.advanced)
        }
        .frame(width: 760, height: 620)
    }
}

// MARK: - Layout

/// A labelled row: the label right-aligned in a fixed column, as the canvas
/// lays settings out, with an optional caption under the control.
struct SettingRow<Control: View>: View {
    let label: String
    var caption: String? = nil
    @ViewBuilder let control: Control

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            Text(label)
                .font(.system(size: 13, weight: .semibold))
                .frame(width: 170, alignment: .trailing)
            VStack(alignment: .leading, spacing: 5) {
                control
                if let caption {
                    Text(caption)
                        .font(.system(size: 11.5))
                        .foregroundStyle(Color.inkFaint)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct Field: ViewModifier {
    var mono = false
    func body(content: Content) -> some View {
        content
            .textFieldStyle(.plain)
            .font(.system(size: 13, design: mono ? .monospaced : .default))
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(Color.card, in: RoundedRectangle(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.ruleStrong, lineWidth: 1))
    }
}

private struct LockFooter: View {
    let text: String
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "lock").font(.system(size: 11))
            Text(text).font(.system(size: 12))
        }
        .foregroundStyle(Color.inkSoft)
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
    }
}

// MARK: - Capture

struct CapturePane: View {
    @Bindable var watcher: Watcher
    @State private var retained: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SettingRow(label: "Transcript folder",
                           caption: "Read-only, always. unrot lists and reads these files; nothing in it ever writes to ~/.claude.") {
                    Text("~/.claude/projects").modifier(Field(mono: true)).frame(maxWidth: 360, alignment: .leading)
                }
                SettingRow(label: "Watch for new sessions",
                           caption: "FSEvents, not polling. Runs whether or not the window is open, and costs nothing while you work.") {
                    Toggle(watcher.paused ? "Off" : "On", isOn: Binding(get: { !watcher.paused }, set: { watcher.paused = !$0 }))
                        .toggleStyle(.switch).tint(Color.watching)
                }
                SettingRow(label: "Take a session once quiet for",
                           caption: "The detector judges what you said after the term was used. While a session is live that turn does not exist yet — and re-reading a growing transcript costs tokens every time.") {
                    HStack(spacing: 8) {
                        TextField("", value: $watcher.quietMinutes, format: .number)
                            .modifier(Field()).frame(width: 60)
                        Stepper("minutes", value: $watcher.quietMinutes, in: 2...60).labelsHidden()
                        Text("minutes").font(.system(size: 13))
                    }
                }
                SettingRow(label: "") {
                    Label("Never takes a project while Claude Code is still running in it", systemImage: "checkmark.square.fill")
                        .font(.system(size: 13))
                        .foregroundStyle(Color.inkPrimary)
                }
                SettingRow(label: "Analyse automatically",
                           caption: "Analysis sends a session's windows to your model and costs what that costs. Off, captured sessions wait for Analyse now. On, it applies only to sessions that finish after you switch it on — anything already waiting still waits for you.") {
                    Toggle(watcher.autoAnalyse ? "On" : "Off", isOn: $watcher.autoAnalyse)
                        .toggleStyle(.switch).tint(Color.watching)
                        .disabled(watcher.paused)
                }
                SettingRow(label: "Queue") { QueueCard(watcher: watcher) }
                SettingRow(label: "Retained copies",
                           caption: "Kept so the moment view can replay what was said, and so a better detector can re-read your history.") {
                    HStack(spacing: 10) {
                        Text("\(retained ?? "…") in ~/.unrot/raw").font(.system(size: 13))
                        Button("Reveal in Finder") { NSWorkspace.shared.activateFileViewerSelecting([Self.raw]) }
                            .buttonStyle(UnrotButton())
                    }
                }
                SettingRow(label: "From any app",
                           caption: "macOS lists third-party items under Services, not at the top of the right-click menu. Its shortcut belongs to the service — ⌥⌘U is the suggested one — so unrot never needs Accessibility permission.") {
                    HStack(spacing: 10) {
                        Text("Select text › right-click › Services › Add to unrot").font(.system(size: 13))
                        Button("Set a shortcut…") {
                            if let url = URL(string: "x-apple.systempreferences:com.apple.Keyboard-Settings.extension") {
                                NSWorkspace.shared.open(url)
                            }
                        }
                        .buttonStyle(UnrotButton())
                    }
                }
                SettingRow(label: "At login") { OpenAtLogin() }
                LockFooter(text: "Everything under raw/ never syncs, never uploads, never leaves this machine. The sync boundary is a path prefix, not a setting you have to trust.")
            }
            .padding(24)
        }
        .background(Color.paper)
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

private struct QueueCard: View {
    let watcher: Watcher

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(summary).font(.system(size: 13, weight: .semibold))
                Spacer()
                if watcher.isRunning {
                    Button("Stop") { watcher.stopAnalysing() }.buttonStyle(UnrotButton())
                } else {
                    Button(watcher.paused ? "Resume" : "Pause") { watcher.paused.toggle() }.buttonStyle(UnrotButton())
                    Button("Analyse now") { watcher.analyseNow() }
                        .buttonStyle(UnrotButton(weight: .primary))
                        .disabled(!watcher.canAnalyse || watcher.pendingCount == 0)
                }
            }
            if let progress = watcher.progress {
                ProgressView(value: Double(progress.done), total: Double(max(progress.total, 1)))
                    .tint(Color.bucketOpen)
            }
            if let pending = watcher.queue?.pending, !pending.isEmpty {
                Text(pending.prefix(3).map { "\($0.repo ?? $0.sessionId.prefix(8).description) · \($0.humanTurns) turns" }.joined(separator: "   "))
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(Color.inkFaint)
                    .lineLimit(1)
            }
            if !watcher.canAnalyse && watcher.pendingCount > 0 {
                Text("No model is configured, so these can't be analysed yet. Add one under Model.")
                    .font(.system(size: 11.5)).foregroundStyle(Color.inkSoft)
            }
            if let error = watcher.lastError {
                Text(error).font(.system(size: 11.5)).foregroundStyle(Color.inkSoft)
            }
        }
        .padding(14)
        .background(Color.card, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.rule, lineWidth: 1))
    }

    private var summary: String {
        let n = watcher.pendingCount
        if let progress = watcher.progress { return "Analysing \(progress.done + 1) of \(progress.total)" }
        if n == 0 { return "Nothing waiting" }
        return "\(n) session\(n == 1 ? "" : "s") waiting" + (watcher.paused ? " · paused" : "")
    }
}

// MARK: - Model

struct ModelPane: View {
    @Bindable var settings: ModelSettings
    let client: UnrotClient
    let watcher: Watcher
    let regenerator: Regenerator
    let restartCore: () -> Void
    let showPlan: () -> Void

    @State private var key = ""
    @State private var inEffect: CoreConfig?
    @State private var confirming = false

    var body: some View {
        VStack(spacing: 18) {
            HStack(alignment: .top, spacing: 22) {
                choices.frame(maxWidth: .infinity)
                leaves.frame(width: 320)
            }
            Spacer(minLength: 0)
            if settings.changed {
                banner(
                    title: "Changes apply when the core restarts.",
                    text: "The core reads its model settings when it starts.",
                    primary: ("Restart the core", {
                        restartCore()
                        settings.applied()
                        Task { try? await Task.sleep(for: .seconds(3)); inEffect = try? await client.config() }
                    }),
                    secondary: nil
                )
            } else if let previously, previously > 0, let plan = regenerator.plan {
                banner(
                    title: "The detector changed. Re-examine your history?",
                    text: "\(previously) session\(previously == 1 ? "" : "s") were analysed by an older detector. \(plan.protected) of your judgments are protected and replayed untouched. Improving the engine improves the whole history, not only what comes next.",
                    primary: ("Re-examine", { confirming = true }),
                    secondary: ("Show the plan", showPlan)
                )
            }
        }
        .padding(24)
        .background(Color.paper)
        .task {
            inEffect = try? await client.config()
            await regenerator.refresh()
        }
        .confirmationDialog("Re-examine \(regenerator.plan?.toRun.count ?? 0) sessions?", isPresented: $confirming) {
            Button("Re-examine") { regenerator.start() }
        } message: {
            Text("This makes model calls for each session, and costs what that costs. It stops between any two sessions, and your judgments are not touched.")
        }
    }

    /// Sessions a regeneration would re-run that were already analysed once --
    /// i.e. by a detector that has since changed. The never-analysed ones are
    /// the queue's business, not this banner's.
    private var previously: Int? {
        guard let plan = regenerator.plan else { return nil }
        let never = watcher.queue?.pending.filter { $0.reason == "never" }.count ?? 0
        return max(0, plan.toRun.count - never)
    }

    private var choices: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Where analysis runs").font(.system(size: 13, weight: .semibold))
                Picker("", selection: $settings.endpoint) {
                    Text("OpenRouter").tag(ModelSettings.Endpoint.hosted)
                    Text("On this Mac").tag(ModelSettings.Endpoint.local)
                    Text("Custom endpoint").tag(ModelSettings.Endpoint.custom)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                if settings.endpoint == .local {
                    TextField("", text: $settings.localURL, prompt: Text("http://localhost:11434/v1").foregroundStyle(Color.inkFaint)).modifier(Field(mono: true))
                    Text("Any OpenAI-compatible server on this Mac — Ollama, LM Studio, llama.cpp.")
                        .font(.system(size: 11.5)).foregroundStyle(Color.inkFaint)
                } else if settings.endpoint == .custom {
                    TextField("", text: $settings.customURL, prompt: Text("https://…/v1").foregroundStyle(Color.inkFaint)).modifier(Field(mono: true))
                }
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("Model").font(.system(size: 13, weight: .semibold))
                TextField("", text: $settings.model, prompt: Text(inEffect.map { "default: \($0.model)" } ?? "the core's default").foregroundStyle(Color.inkFaint))
                    .modifier(Field(mono: true))
                Text("Used by the detector, the resolver and material. The detector reads transcript windows, so this line is where the bill actually lives. Leave it empty for the core's default.")
                    .font(.system(size: 11.5)).foregroundStyle(Color.inkFaint)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if settings.endpoint != .local {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Reasoning effort").font(.system(size: 13, weight: .semibold))
                    Picker("", selection: $settings.effort) {
                        ForEach(ModelSettings.Effort.allCases) { Text($0.label).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    Text("How long a reasoning model thinks before it answers. More can find subtler gaps; it is also slower and costs more, and on a long session it can run out of room before answering. Ignored by models that do not reason.")
                        .font(.system(size: 11.5)).foregroundStyle(Color.inkFaint)
                        .fixedSize(horizontal: false, vertical: true)
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text("API key").font(.system(size: 13, weight: .semibold))
                    HStack(spacing: 8) {
                        SecureField("", text: $key, prompt: Text(settings.hasKey ? "A key is stored — paste to replace it" : "Paste your OpenRouter key").foregroundStyle(Color.inkFaint))
                            .modifier(Field(mono: true))
                        Button("Save") { settings.saveKey(key); key = "" }
                            .buttonStyle(UnrotButton(weight: .primary))
                            .disabled(key.trimmingCharacters(in: .whitespaces).isEmpty)
                        if settings.hasKey {
                            Button("Remove") { settings.removeKey() }.buttonStyle(UnrotButton())
                        }
                    }
                    Text("Kept in the login Keychain, not in a .env beside your code. A key set in your shell still wins.")
                        .font(.system(size: 11.5)).foregroundStyle(Color.inkFaint)
                }
            }
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Grader").font(.system(size: 13, weight: .semibold))
                    Text("A classifier, separate from the model above. It returns a distribution, which is what keeps the listed/causal line movable later.")
                        .font(.system(size: 11.5)).foregroundStyle(Color.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                if let inEffect {
                    // "configured", not "reachable": nothing here probes it, and
                    // a status should not claim a check that never ran.
                    Pip(text: inEffect.grader == "classifier" ? "configured" : "keyword only",
                        tint: inEffect.grader == "classifier" ? .bucketClosed : .bucketOpen,
                        wash: inEffect.grader == "classifier" ? .bucketClosedBG : .bucketOpenBG)
                }
            }
            .padding(12)
            .background(Color.card, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.rule, lineWidth: 1))
        }
    }

    /// What leaves, told beside the choice that changes it. The list comes from
    /// the core -- it lives next to the code that makes the calls.
    private var leaves: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("What leaves this Mac, right now").font(.system(size: 13.5, weight: .semibold))
            if let inEffect {
                if inEffect.leavesThisMac.isEmpty {
                    Eyebrow(text: "Nothing — every call stays on this Mac", tint: .bucketClosed)
                } else {
                    Eyebrow(text: "Sent to \(host(inEffect.baseUrl))", tint: .bucketOpen)
                    ForEach(inEffect.leavesThisMac, id: \.self) { line in
                        HStack(alignment: .firstTextBaseline, spacing: 7) {
                            Image(systemName: "arrow.right").font(.system(size: 10)).foregroundStyle(Color.bucketOpen)
                            Text(line).font(.system(size: 12)).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            } else {
                Text("The core isn't answering.").font(.system(size: 12)).foregroundStyle(Color.inkFaint)
            }
            Divider().padding(.vertical, 2)
            Eyebrow(text: "Never sent, on any setting", tint: .bucketClosed)
            ForEach(["The retained transcript archive under raw/.",
                     "Your confirmations, dismissals and answers, as a record.",
                     "The event log itself, or anything compiled from it."], id: \.self) { line in
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    Image(systemName: "lock").font(.system(size: 10)).foregroundStyle(Color.bucketClosed)
                    Text(line).font(.system(size: 12)).fixedSize(horizontal: false, vertical: true)
                }
            }
            Text("Switch to On this Mac and the top list empties. That is the whole privacy story — one setting, not a rewrite.")
                .font(.system(size: 11.5)).foregroundStyle(Color.inkFaint)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 4)
        }
        .padding(16)
        .background(Color.card, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.rule, lineWidth: 1))
    }

    private func host(_ url: String) -> String {
        let name = URL(string: url)?.host() ?? url
        return name.contains("openrouter") ? "OpenRouter" : name
    }

    private func banner(
        title: String, text: String,
        primary: (String, () -> Void), secondary: (String, () -> Void)?
    ) -> some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 13.5, weight: .semibold)).foregroundStyle(Color.bucketLearning)
                Text(text).font(.system(size: 12)).foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if let secondary { Button(secondary.0, action: secondary.1).buttonStyle(UnrotButton()) }
            Button(primary.0, action: primary.1).buttonStyle(UnrotButton(weight: .primary))
        }
        .padding(16)
        .background(Color.bucketLearningBG, in: RoundedRectangle(cornerRadius: 10))
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
