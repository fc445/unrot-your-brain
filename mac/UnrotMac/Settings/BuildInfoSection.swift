//  BuildInfoSection.swift
//  UnrotMac
//
//  Dev-channel builds only: the top of the Developer tab. What this build is and
//  where it keeps things, for whoever is testing a dev DMG. See "Dev and prod
//  builds" in mac/README.md.

#if DEV_FEATURES
import AppKit
import SwiftUI

struct BuildInfoSection: View {
    var body: some View {
        let home = CoreProcess.unrotHome()
        Section {
            LabeledContent("Channel", value: Self.info("UnrotChannel"))
            LabeledContent("Version",
                           value: "\(Self.info("CFBundleShortVersionString")) (\(Self.info("CFBundleVersion")))")
            LabeledContent("Configuration", value: Self.configuration)
            LabeledContent("Store", value: home.path)
            HStack(spacing: 10) {
                Button("Reveal store in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([home])
                }
                Button("Open core log") {
                    NSWorkspace.shared.open(home.appending(path: "run/core.log"))
                }
            }
        } header: {
            Text("This build")
        } footer: {
            Text("This tab, and everything else behind DEV_FEATURES, is compiled out of prod builds.")
                .font(.system(size: 11))
                .foregroundStyle(Color.inkFaint)
        }
    }

    private static func info(_ key: String) -> String {
        Bundle.main.object(forInfoDictionaryKey: key) as? String ?? "?"
    }

    private static var configuration: String {
        #if DEBUG
        return "Debug"
        #else
        return "Release"
        #endif
    }
}
#endif
