//  PanelFit.swift
//  UnrotMac
//
//  Keeps a floating panel the size of its SwiftUI content.
//
//  `NSHostingController.sizingOptions = [.preferredContentSize]` does this for
//  free, but it reads the SwiftUI size from inside `updateViewConstraints`,
//  which invalidates constraints while AppKit is already updating them, and
//  AppKit throws ("setNeedsUpdateConstraints on a view already being laid
//  out"). Sizing from outside the layout pass, one turn later, does not.

import AppKit
import Observation

@MainActor
enum PanelFit {
    static func fit(_ panel: NSPanel, to hosting: NSViewController) {
        panel.setContentSize(hosting.view.fittingSize)
    }

    /// Re-fits whenever anything `read` touches changes. `read` should only
    /// touch the state that changes the content's size.
    static func follow(_ panel: NSPanel, to hosting: NSViewController, reading read: @escaping @MainActor () -> Void) {
        withObservationTracking {
            read()
        } onChange: { [weak panel, weak hosting] in
            Task { @MainActor [weak panel, weak hosting] in
                // `onChange` fires before the view has the new state.
                try? await Task.sleep(for: .milliseconds(30))
                guard let panel, let hosting else { return }
                fit(panel, to: hosting)
                follow(panel, to: hosting, reading: read)
            }
        }
    }
}
