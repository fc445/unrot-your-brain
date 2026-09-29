//  UnwindMark.swift
//  UnrotMac
//
//  The mark, "Unwind": a spiral that straightens into a line -- from skimming
//  to understanding in one stroke. The same drawing as the app icon and the
//  menu-bar glyph, in the same units (see mac/Brand/), so the three cannot
//  drift apart.
//
//  An InsettableShape, so `strokeBorder` keeps the stroke inside the frame it
//  is given, and a caller sizes the mark by its frame and nothing else.

import SwiftUI

struct UnwindMark: InsettableShape {
    /// The mark's extent in its own units: x 40...92, y 35...60.
    static let aspectRatio: CGFloat = 52 / 25

    var insetAmount: CGFloat = 0

    /// Round caps and joins: the mark has no corners.
    static func strokeStyle(lineWidth: CGFloat) -> StrokeStyle {
        StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round)
    }

    func path(in rect: CGRect) -> Path {
        let box = rect.insetBy(dx: insetAmount, dy: insetAmount)
        guard box.width > 0, box.height > 0 else { return Path() }

        // Fit the mark's 52 x 25 units into the box, centred, keeping its shape.
        let scale = min(box.width / 52, box.height / 25)
        let origin = CGPoint(
            x: box.midX - (40 + 92) / 2 * scale,
            y: box.midY - (35 + 60) / 2 * scale
        )
        func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: origin.x + x * scale, y: origin.y + y * scale)
        }

        // y runs down, so increasing angle is clockwise on screen -- the way
        // the spiral turns. Three turns, ending at the top of the outer one,
        // pointing right, where the line leaves.
        var path = Path()
        path.move(to: point(50, 50))
        path.addArc(center: point(55, 50), radius: 5 * scale,
                    startAngle: .degrees(180), endAngle: .degrees(360), clockwise: false)
        path.addArc(center: point(50, 50), radius: 10 * scale,
                    startAngle: .degrees(0), endAngle: .degrees(180), clockwise: false)
        path.addArc(center: point(55, 50), radius: 15 * scale,
                    startAngle: .degrees(180), endAngle: .degrees(270), clockwise: false)
        path.addLine(to: point(92, 35))
        return path
    }

    func inset(by amount: CGFloat) -> UnwindMark {
        var mark = self
        mark.insetAmount += amount
        return mark
    }
}

/// The mark and the name, set together. Sized by the name's point size; the
/// mark follows it.
struct Wordmark: View {
    var size: CGFloat = 15
    var tint: Color = .accent

    var body: some View {
        HStack(spacing: size * 0.35) {
            UnwindMark()
                .strokeBorder(tint, style: UnwindMark.strokeStyle(lineWidth: max(1.5, size * 0.11)))
                .frame(width: size * 0.72 * UnwindMark.aspectRatio, height: size * 0.72)
            Text("unrot")
                .font(.system(size: size, weight: .semibold))
                .tracking(-size * 0.03)
                .foregroundStyle(Color.inkPrimary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("unrot")
    }
}
