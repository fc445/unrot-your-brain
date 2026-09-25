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
            SettingsView(
                notifier: delegate.notifier,
                watcher: delegate.watcher,
                model: delegate.model,
                regenerator: delegate.regenerator,
                client: delegate.client,
                restartCore: { delegate.core.restart() }
            )
            #if DEV_FEATURES
            .environment(delegate.langSmith)
            #endif
        }
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Open unrot") { delegate.showMain() }
                    .keyboardShortcut("0")
                Button("Triage") { delegate.showTriage() }
                    .keyboardShortcut("j", modifiers: [.command, .option])
                Button("Add a Gap…") { delegate.addGap() }
                    .keyboardShortcut("n")
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
    let model = ModelSettings()
    #if DEV_FEATURES
    let langSmith = LangSmithSettings()
    #endif
    lazy var client = UnrotClient(socketPath: core.socketPath)
    lazy var store = SurfaceStore(client: client)
    lazy var quick = QuickAccept(store: store)
    lazy var watcher = Watcher(client: client, store: store, core: core,
                               concurrency: { [model] in model.concurrency })
    lazy var regenerator = Regenerator(client: client, store: store,
                                       concurrency: { [model] in model.concurrency })
    let router = Router()
    let onboarding = Onboarding()

    private lazy var main = MainWindow { [unowned self] in
        AnyView(
            RootView(
                core: core, store: store, quick: quick, watcher: watcher, router: router,
                addGap: { [unowned self] in self.addGap() },
                onboarding: onboarding, modelSettings: model
            )
                .frame(minWidth: 820, minHeight: 520)
        )
    }
    private var statusItem: StatusItemController?
    lazy var notifier = Notifier(
        store: store,
        quick: quick,
        openMain: { [weak self] in self?.showMain() },
        openTriage: { [weak self] in self?.showTriage() }
    )
    private lazy var triage = TriagePanel(store: store, quick: quick)
    private lazy var capture = CapturePanel(store: store)
    private lazy var service = CaptureService { [weak self] selection, app in
        self?.capture.open(selection: selection, from: app)
    }
    private var triageKey: HotKey?

    func applicationDidFinishLaunching(_ notification: Notification) {
        #if DEBUG
        if Snapshots.requested {
            Task { await Snapshots.run() }
            return
        }
        #endif
        #if DEV_FEATURES
        core.environmentProvider = { [model, langSmith] in
            MainActor.assumeIsolated {
                // UNROT_DEV_FEATURES gates `POST /api/dev/wipe` in the core
                // (see `_dev_mode` in api/app.py) -- set here, unconditionally
                // in a dev build, rather than behind a setting of its own,
                // because a dev build sharing the real `~/.unrot` with prod is
                // exactly the situation that endpoint refuses to run in
                // otherwise. `current` (the model/LangSmith values) always wins
                // a key collision, and neither of those ever sets this one.
                ["UNROT_DEV_FEATURES": "1"]
                    .merging(model.environment()) { current, _ in current }
                    .merging(langSmith.environment()) { current, _ in current }
            }
        }
        #else
        core.environmentProvider = { [model] in
            MainActor.assumeIsolated { model.environment() }
        }
        #endif
        core.start()
        statusItem = StatusItemController(
            store: store,
            core: core,
            quick: quick,
            watcher: watcher,
            actions: .init(
                router: router,
                openMain: { [weak self] in self?.showMain() },
                addGap: { [weak self] in self?.capture.openBlank() }
            )
        )
        notifier.start()
        // Nothing is captured until the first run has said what will be read
        // and the user has chosen to start.
        if onboarding.done {
            watcher.start()
        } else {
            onboarding.onFinish = { [weak self] in
                self?.core.restart()   // pick up a key or endpoint chosen during setup
                self?.watcher.start()
            }
        }
        NSApp.servicesProvider = service
        // Re-reads the Services declarations, so "Add to unrot" appears without
        // logging out after the app is installed or moved.
        NSUpdateDynamicServices()
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

    func addGap() { capture.openBlank() }

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
