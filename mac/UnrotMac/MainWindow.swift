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

    private func build() -> NSWindow {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1040, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "unrot"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.isReleasedWhenClosed = false
        window.contentMinSize = NSSize(width: 820, height: 520)
        window.contentView = NSHostingView(rootView: content())
        window.delegate = self
        if !window.setFrameUsingName("unrot.main") { window.center() }
        window.setFrameAutosaveName("unrot.main")
        return window
    }

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}
