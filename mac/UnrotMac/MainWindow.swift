//  MainWindow.swift
//  UnrotMac
//
//  The window, owned by AppKit rather than by a SwiftUI scene.
//
//  Once the menu-bar item exists, closing the window must not quit the app --
//  the ring is what is left running -- and something that is not a SwiftUI view
//  has to be able to bring it back: the right-click menu, the Dock, a
//  notification. A `WindowGroup` destroys its window on close and can only be
//  reopened from inside SwiftUI; an `NSWindow` held here can be shown from
//  anywhere.

import AppKit
import SwiftUI
import UnrotKit

@MainActor
final class MainWindow: NSObject, NSWindowDelegate {
    private var window: NSWindow?
    private let content: () -> AnyView

    init(content: @escaping () -> AnyView) {
        self.content = content
    }

    func show() {
        let window = window ?? build()
        self.window = window
        // A Dock icon while the window is open, none while only the ring is.
        // An ambient tool should not sit in the app switcher doing nothing.
        NSApp.setActivationPolicy(.regular)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate()
    }

    /// On screen but behind every other window, and without taking focus:
    /// for a tool to capture it while someone is using the Mac for something
    /// else. It draws as an inactive window does, because that is what it is.
    func showBehind() {
        let window = window ?? build()
        self.window = window
        window.orderBack(nil)
    }

    private func build() -> NSWindow {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1040, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.toolbarStyle = .unified
        window.isReleasedWhenClosed = false
        window.contentMinSize = NSSize(width: 820, height: 520)
        let hosting = NSHostingView(rootView: content())
        // Hands the page's `.toolbar`, title and subtitle to this window.
        // Without it they would go nowhere: SwiftUI only puts them in windows
        // its own scenes made. A view rather than a hosting controller, because
        // a window binds its title to its content view controller's, and that
        // binding would overwrite the bridged one with nothing.
        hosting.sceneBridgingOptions = [.toolbars, .title]
        window.contentView = hosting
        window.delegate = self
        if !window.setFrameUsingName("unrot.main") { window.center() }
        window.setFrameAutosaveName("unrot.main")
        return window
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}
