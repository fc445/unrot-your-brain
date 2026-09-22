//  TrayGlyph.swift
//  UnrotMac
//
//  One glyph, five states, and never a red dot.
//
//  The icon carries the state rather than badging it: a closed ring means
//  nothing is waiting, a broken ring means something is, and the count sits
//  beside it as plain text. Drawn once as a template image, so macOS handles
//  light and dark, reduced transparency and the accent colour -- and since a
//  template is monochrome by construction, red is not something it can be.
//
//  Paused and clean are deliberately unlike each other. A watcher someone
//  switched off must never look like a clean week.

import AppKit

enum TrayState: Equatable {
    case clean
    case waiting(Int)
    /// A session is being analysed. The only state that animates.
    case analysing
    case paused
    case coreDown

    var accessibilityLabel: String {
        switch self {
        case .clean: "unrot — nothing waiting"
        case .waiting(let n): "unrot — \(n) waiting"
        case .analysing: "unrot — analysing a session"
        case .paused: "unrot — watching paused"
        case .coreDown: "unrot — the core is not running"
        }
    }
}

enum TrayGlyph {
    static let size = NSSize(width: 18, height: 18)
    private static let center = NSPoint(x: 9, y: 9)
    private static let radius: CGFloat = 6
    private static let stroke: CGFloat = 1.5

    /// `phase` only matters for `.analysing`; it walks the dashes round.
    static func image(for state: TrayState, phase: CGFloat = 0) -> NSImage {
        let image = NSImage(size: size, flipped: false) { _ in
            draw(state, phase: phase)
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = state.accessibilityLabel
        return image
    }

    private static func draw(_ state: TrayState, phase: CGFloat) {
        // Template images keep alpha and discard colour, so "quieter" is alpha.
        let ink = NSColor.black
        let ring = NSBezierPath()
        ring.lineWidth = stroke
        ring.lineCapStyle = .round

        switch state {
        case .clean:
            ring.appendOval(in: circleRect)
            ink.setStroke()

        case .waiting:
            // Broken at the upper right: a 100° gap, the same proportion as the
            // canvas's 36/14 dash on a 50-unit circumference.
            ring.appendArc(withCenter: center, radius: radius, startAngle: 90, endAngle: 350, clockwise: false)
            ink.setStroke()

        case .analysing:
            ring.appendOval(in: circleRect)
            ring.setLineDash([2.2, 3.3], count: 2, phase: phase)
            ink.setStroke()

        case .paused:
            ring.appendOval(in: circleRect)
            ring.move(to: NSPoint(x: 5.1, y: 5.1))
            ring.line(to: NSPoint(x: 12.9, y: 12.9))
            ink.withAlphaComponent(0.55).setStroke()

        case .coreDown:
            // Dots, faint. Not an alarm -- the window is where the failure is
            // explained; the menu bar only has to not look healthy.
            ring.appendOval(in: circleRect)
            ring.setLineDash([0.01, 3.0], count: 2, phase: 0)
            ink.withAlphaComponent(0.5).setStroke()
        }
        ring.stroke()
    }

    private static var circleRect: NSRect {
        NSRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)
    }
}
