//  UnrotMacApp.swift
//  UnrotMac

import AppKit
import Carbon.HIToolbox
import SwiftUI
import UnrotKit

@main
struct UnrotMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        // The window is AppKit's (see MainWindow). This scene exists for the
        // menu bar commands and, in phase 5, the settings panes.
        Settings {
            Text("Settings arrive in phase 5.")
                .padding(40)
        }
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Open unrot") { delegate.showMain() }
                    .keyboardShortcut("0")
                Button("Triage") { delegate.showTriage() }
                    .keyboardShortcut("j", modifiers: [.command, .option])
            }
            CommandGroup(after: .toolbar) {
                Button("Reload") { Task { await delegate.store.load() } }
                    .keyboardShortcut("r")
                Button("Restart the Core") { delegate.core.restart() }
                    .keyboardShortcut("r", modifiers: [.command, .shift])
                Divider()
                Button("Show in Menu Bar") { delegate.showStatusItem() }
            }
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let core = CoreProcess()
    lazy var store = SurfaceStore(client: UnrotClient(socketPath: core.socketPath))
    lazy var quick = QuickAccept(store: store)

    private lazy var main = MainWindow { [unowned self] in
        AnyView(
            RootView(core: core, store: store, quick: quick)
                .frame(minWidth: 620, minHeight: 480)
        )
    }
    private var statusItem: StatusItemController?
    private lazy var triage = TriagePanel(store: store, quick: quick)
    private var triageKey: HotKey?

    func applicationDidFinishLaunching(_ notification: Notification) {
        core.start()
        statusItem = StatusItemController(
            store: store,
            core: core,
            quick: quick,
            actions: .init(openMain: { [weak self] in self?.showMain() })
        )
        // ⌥⌘J, system-wide. If another app already owns the chord this is
        // nil, and triage is still reachable from the app menu.
        triageKey = HotKey(keyCode: kVK_ANSI_J, modifiers: cmdKey | optionKey) { [weak self] in
            self?.triage.toggle()
        }
        showMain()
        // The ring needs a surface to show a count, even while the window is
        // closed, so the store is kept fresh here rather than only by the view.
        Task { await refreshForever() }
    }

    func showMain() { main.show() }

    func showTriage() { triage.show() }

    func showStatusItem() { statusItem?.isVisible = true }

    private func refreshForever() async {
        while !Task.isCancelled {
            if core.status.isUp { await store.load() }
            try? await Task.sleep(for: .seconds(15))
        }
    }

    /// Clicking the Dock icon with no window open brings the window back.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows { showMain() }
        return true
    }

    /// The core is ours, so it goes when we do. SIGTERM is what uvicorn
    /// handles, and handling it is what unlinks the socket -- so quitting
    /// cleanly here is what spares the next launch a stale-socket reclaim.
    func applicationWillTerminate(_ notification: Notification) {
        core.stop()
    }

    /// The ring stays when the window goes. Quitting is in its right-click
    /// menu and in the app menu.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}
