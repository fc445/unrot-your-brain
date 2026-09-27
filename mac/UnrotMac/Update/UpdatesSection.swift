//  UpdatesSection.swift
//  UnrotMac
//
//  Settings › Advanced: what this build is, whether it checks for updates by
//  itself, and a button to check now. The Keychain sentence is here because it
//  is the one surprising thing about an update until there is a Developer ID:
//  macOS ties the saved API key to the exact build that saved it (PR-35).

import SwiftUI

struct UpdatesSection: View {
    @Bindable var updater: Updater

    var body: some View {
        Section {
            LabeledContent("Version", value: Self.version)
            if let reason = updater.disabledReason {
                Text(reason).foregroundStyle(Color.inkSoft)
            } else {
                Toggle("Check for updates automatically", isOn: $updater.checksAutomatically)
                HStack {
                    Button("Check Now") { updater.checkForUpdates() }
                        .disabled(!updater.canCheck)
                    if let last = updater.lastChecked {
                        Text("Last checked \(last.formatted(.relative(presentation: .named)))")
                            .font(.system(size: 11))
                            .foregroundStyle(Color.inkFaint)
                    }
                }
            }
        } header: {
            Text("Updates")
        } footer: {
            if updater.disabledReason == nil {
                VStack(alignment: .leading, spacing: 6) {
                    Text(channelLine)
                    Text("After an update, macOS asks once whether unrot may use your saved API key. Choose Always Allow. It asks because each build is signed on its own; a signed release will stop it.")
                }
                .font(.system(size: 11))
                .foregroundStyle(Color.inkFaint)
            }
        }
    }

    private var channelLine: String {
        updater.channel == "dev"
            ? "This is a dev build, so it updates to dev releases only."
            : "Updates come from unrot's GitHub releases, checked once a day."
    }

    private static var version: String {
        let info = Bundle.main.infoDictionary ?? [:]
        let short = info["CFBundleShortVersionString"] as? String ?? "?"
        let build = info["CFBundleVersion"] as? String ?? "?"
        return "\(short) (\(build))"
    }
}
