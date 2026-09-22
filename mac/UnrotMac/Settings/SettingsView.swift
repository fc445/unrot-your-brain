//  SettingsView.swift
//  UnrotMac
//
//  The settings window. Phase 5 adds the capture folder, the model and the key;
//  this is what phases 4 and 4b need.

import SwiftUI
import UnrotKit

struct SettingsView: View {
    let notifier: Notifier

    var body: some View {
        TabView {
            NotificationsPane(notifier: notifier)
                .tabItem { Label("Notifications", systemImage: "bell") }
            CapturePane()
                .tabItem { Label("Capture", systemImage: "text.cursor") }
        }
        .frame(width: 520, height: 360)
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
