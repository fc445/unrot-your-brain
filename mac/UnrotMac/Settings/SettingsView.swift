//  SettingsView.swift
//  UnrotMac
//
//  The settings window. Phase 5 adds the capture folder, the model and the key;
//  this is what phases 4 and 4b need.

import SwiftUI
import UnrotKit

struct SettingsView: View {
    let notifier: Notifier
    let watcher: Watcher

    var body: some View {
        TabView {
            WatchingPane(watcher: watcher)
                .tabItem { Label("Watching", systemImage: "eye") }
            NotificationsPane(notifier: notifier)
                .tabItem { Label("Notifications", systemImage: "bell") }
            CapturePane()
                .tabItem { Label("Capture", systemImage: "text.cursor") }
        }
        .frame(width: 540, height: 460)
    }
}

private struct WatchingPane: View {
    @Bindable var watcher: Watcher

    var body: some View {
        Form {
            Section {
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

private struct NotificationsPane: View {
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

private struct CapturePane: View {
    var body: some View {
        Form {
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
    }
}
