//  TrayGlyph.swift
//  UnrotMac
//
//  One glyph, five states, and never a red dot.
//
//  The glyph is the app's mark, "Unwind": a spiral that straightens into a
//  line. The icon carries the state rather than badging it: an unbroken line
//  means nothing is waiting, a line with a break in it means something is, and
//  the count sits beside it as plain text. Drawn once as a template image, so
//  macOS handles light and dark, reduced transparency and the accent colour --
//  and since a template is monochrome by construction, red is not something it
//  can be.
//
//  Paused and clean are deliberately unlike each other. A watcher someone
//  switched off must never look like a clean week.
//
//  The geometry is the mark's own, in its own units (see mac/Brand/), scaled
//  down: the app icon, UnwindMark and this are one drawing at three sizes.

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
    /// Wider than tall, as the mark is. A status item takes its width from its
    /// image, and the menu bar leaves 18 pt of height for it.
    static let size = NSSize(width: 23, height: 18)
    private static let stroke: CGFloat = 1.5

    // Mark units to points. The mark spans x 40...86 and y 35...60 here (the
    // line is a little shorter than the icon's, to fit the menu bar); at 0.45
    // the turns sit 4.5 pt apart, which leaves a clear 3 pt between strokes.
    private static let scale: CGFloat = 0.45
    private static let origin = NSPoint(
        x: (size.width - (86 - 40) * scale) / 2 - 40 * scale,
        y: size.height / 2 - (35 + 60) / 2 * scale
    )

    private static func point(_ x: CGFloat, _ y: CGFloat) -> NSPoint {
        NSPoint(x: origin.x + x * scale, y: origin.y + y * scale)
    }

    /// `phase` only matters for `.analysing`; it walks the dashes along.
    static func image(for state: TrayState, phase: CGFloat = 0) -> NSImage {
        // Flipped, so y runs down as it does in the mark's own units.
        let image = NSImage(size: size, flipped: true) { _ in
            draw(state, phase: phase)
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = state.accessibilityLabel
        return image
    }

    /// The spiral: three half-and-quarter turns ending pointing right, at the
    /// top of the outer turn, where the line leaves. Angles run the way y
    /// does, down, so increasing angle is clockwise on screen.
    private static func spiral(into path: NSBezierPath) {
        path.move(to: point(50, 50))
        path.appendArc(withCenter: point(55, 50), radius: 5 * scale, startAngle: 180, endAngle: 360, clockwise: false)
        path.appendArc(withCenter: point(50, 50), radius: 10 * scale, startAngle: 0, endAngle: 180, clockwise: false)
        path.appendArc(withCenter: point(55, 50), radius: 15 * scale, startAngle: 180, endAngle: 270, clockwise: false)
    }

    private static func draw(_ state: TrayState, phase: CGFloat) {
        // Template images keep alpha and discard colour, so "quieter" is alpha.
        let ink = NSColor.black
        let mark = NSBezierPath()
        mark.lineWidth = stroke
        mark.lineCapStyle = .round
        mark.lineJoinStyle = .round
        spiral(into: mark)

        switch state {
        case .clean:
            mark.line(to: point(86, 35))
            ink.setStroke()

        case .waiting:
            // The line, broken: not yet straight.
            mark.line(to: point(66, 35))
            mark.move(to: point(74, 35))
            mark.line(to: point(86, 35))
            ink.setStroke()

        case .analysing:
            mark.line(to: point(86, 35))
            mark.setLineDash([2.2, 3.3], count: 2, phase: phase)
            ink.setStroke()

        case .paused:
            // The line stops at a pause sign, faded. Nothing like clean.
            mark.line(to: point(64, 35))
            mark.move(to: point(74, 30))
            mark.line(to: point(74, 42))
            mark.move(to: point(83, 30))
            mark.line(to: point(83, 42))
            ink.withAlphaComponent(0.55).setStroke()

        case .coreDown:
            // Dots, faint. Not an alarm -- the window is where the failure is
            // explained; the menu bar only has to not look healthy.
            mark.line(to: point(86, 35))
            mark.setLineDash([0.01, 3.0], count: 2, phase: 0)
            ink.withAlphaComponent(0.5).setStroke()
        }
        mark.stroke()
    }
}
