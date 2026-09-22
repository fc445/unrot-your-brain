//  StatusItemController.swift
//  UnrotMac
//
//  The ring in the menu bar. Left click opens the popover; right click goes
//  straight to a menu.
//
//  It follows the store and the supervisor through Observation rather than a
//  timer, so it changes when they do and at no other time -- a menu-bar item
//  that polls is a menu-bar item that drains a battery to say nothing new.

import AppKit
import Observation
import SwiftUI
import UnrotKit

@MainActor
final class StatusItemController: NSObject {
    private let item: NSStatusItem
    private let popover = NSPopover()
    private let store: SurfaceStore
    private let core: CoreProcess
    private let quick: QuickAccept
    private let actions: Actions

    /// What the right-click menu and the popover can ask the app to do.
    struct Actions {
        var openMain: () -> Void
        var addGap: (() -> Void)?
    }

    private var spinner: Timer?
    private var phase: CGFloat = 0
    private var shown: TrayState?

    init(store: SurfaceStore, core: CoreProcess, quick: QuickAccept, actions: Actions) {
        self.store = store
        self.core = core
        self.quick = quick
        self.actions = actions
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        super.init()

        // Removable by ⌘-dragging it out, and macOS remembers that across
        // relaunches under the autosave name -- the native mechanism for
        // "hiding the icon is allowed and survives relaunch".
        item.behavior = .removalAllowed
        item.autosaveName = "unrot"

        if let button = item.button {
            button.target = self
            button.action = #selector(clicked(_:))
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
            button.imagePosition = .imageLeading
        }

        popover.behavior = .transient
        popover.animates = false
        popover.contentViewController = NSHostingController(
            rootView: TrayPopover(store: store, core: core, quick: quick) { [weak self] in
                self?.popover.performClose(nil)
                self?.actions.openMain()
            }
        )

        render()
        follow()
    }

    var isVisible: Bool {
        get { item.isVisible }
        set { item.isVisible = newValue }
    }

    // MARK: - State

    /// Assembled from three sources, deciding none of them. The count is the
    /// server's; up or down is the supervisor's; the watcher's states arrive
    /// with phase 3.
    private var state: TrayState {
        if !core.status.isUp || store.state == .failed { return .coreDown }
        let waiting = store.waitingCount
        return waiting > 0 ? .waiting(waiting) : .clean
    }

    private func follow() {
        withObservationTracking {
            _ = state
        } onChange: { [weak self] in
            Task { @MainActor in
                self?.render()
                self?.follow()
            }
        }
    }

    private func render() {
        let now = state
        guard now != shown else { return }
        shown = now

        guard let button = item.button else { return }
        button.image = TrayGlyph.image(for: now, phase: phase)
        button.setAccessibilityLabel(now.accessibilityLabel)
        if case .waiting(let n) = now {
            button.attributedTitle = NSAttributedString(
                string: " \(n)",
                attributes: [.font: NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .medium)]
            )
        } else {
            button.title = ""
        }
        animate(now == .analysing)
    }

    /// The one animation, and only while something is being analysed.
    private func animate(_ on: Bool) {
        if on, spinner == nil {
            spinner = Timer.scheduledTimer(withTimeInterval: 0.12, repeats: true) { [weak self] _ in
                Task { @MainActor in
                    guard let self else { return }
                    self.phase = (self.phase + 0.8).truncatingRemainder(dividingBy: 5.5)
                    self.item.button?.image = TrayGlyph.image(for: .analysing, phase: self.phase)
                }
            }
        } else if !on {
            spinner?.invalidate()
            spinner = nil
        }
    }

    // MARK: - Clicks

    @objc private func clicked(_ sender: NSStatusBarButton) {
        if NSApp.currentEvent?.type == .rightMouseUp {
            showMenu()
        } else {
            togglePopover(sender)
        }
    }

    private func togglePopover(_ button: NSStatusBarButton) {
        if popover.isShown {
            popover.performClose(nil)
            return
        }
        Task { await store.load() }
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        // Keys only reach the popover if it is key, and it is only key if the
        // app is active. Without this, D and K would go to whatever app was
        // frontmost before the click.
        NSApp.activate()
        popover.contentViewController?.view.window?.makeKey()
    }

    private func showMenu() {
        let menu = NSMenu()
        if actions.addGap != nil {
            menu.addItem(withTitle: "Add a Gap…", action: #selector(addGap), keyEquivalent: "").target = self
        }
        menu.addItem(withTitle: "Open unrot", action: #selector(openMain), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit unrot", action: #selector(quit), keyEquivalent: "q").target = self

        // The documented way to pop a menu from a status item on demand: attach,
        // click, detach -- so a left click keeps opening the popover.
        item.menu = menu
        item.button?.performClick(nil)
        item.menu = nil
    }

    @objc private func addGap() { actions.addGap?() }
    @objc private func openMain() { actions.openMain() }
    @objc private func quit() { NSApp.terminate(nil) }
}
