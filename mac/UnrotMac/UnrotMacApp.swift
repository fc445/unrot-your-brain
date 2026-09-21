//  UnrotMacApp.swift
//  UnrotMac

import AppKit
import SwiftUI
import UnrotKit

@main
struct UnrotMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        WindowGroup("unrot") {
            RootView(core: delegate.core, store: delegate.store)
                .frame(minWidth: 620, minHeight: 480)
        }
        .defaultSize(width: 880, height: 780)
        .windowToolbarStyle(.unified(showsTitle: false))
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(after: .toolbar) {
                Button("Reload") { Task { await delegate.store.load() } }
                    .keyboardShortcut("r")
                Button("Restart the core") { delegate.core.restart() }
                    .keyboardShortcut("r", modifiers: [.command, .shift])
            }
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let core = CoreProcess()
    lazy var store = SurfaceStore(client: UnrotClient(socketPath: core.socketPath))

    func applicationDidFinishLaunching(_ notification: Notification) {
        core.start()
    }

    /// The core is ours, so it goes when we do. SIGTERM is what uvicorn
    /// handles, and handling it is what unlinks the socket -- so quitting
    /// cleanly here is what spares the next launch a stale-socket reclaim.
    func applicationWillTerminate(_ notification: Notification) {
        core.stop()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        // Until the menu-bar item exists (phase 4), closing the window is how
        // you quit. Leaving a headless process running with nothing to show
        // for it would be worse than the thing phase 4 replaces this with.
        true
    }
}
