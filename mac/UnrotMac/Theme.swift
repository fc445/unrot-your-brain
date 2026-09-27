//  Theme.swift
//  UnrotMac
//
//  The same palette as ui/src/styles.css, ported value for value including the
//  dark set. Two surfaces over one store that do not look like each other are
//  two products, and the difference between them stops being diagnostic.
//
//  The one deliberate departure is the display face. The canvas specifies
//  Instrument Serif; this uses New York, which is Apple's serif and ships with
//  the OS. It reads the same at wordmark size, costs no bundled binary and no
//  font licence in the repo, and a Mac app set in the system serif is the more
//  native answer anyway.

import SwiftUI
import UnrotKit

extension Color {
    /// A colour that resolves per appearance, rather than two stylesheets.
    /// `contrast` is what it becomes under Accessibility › Increase Contrast;
    /// without one, the colour is already strong enough to stay as it is.
    static func ink(_ light: UInt32, _ dark: UInt32, contrast: (light: UInt32, dark: UInt32)? = nil) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            switch appearance.bestMatch(from: [
                .aqua, .darkAqua, .accessibilityHighContrastAqua, .accessibilityHighContrastDarkAqua,
            ]) {
            case .accessibilityHighContrastAqua: NSColor(rgb: contrast?.light ?? light)
            case .accessibilityHighContrastDarkAqua: NSColor(rgb: contrast?.dark ?? dark)
            case .darkAqua: NSColor(rgb: dark)
            default: NSColor(rgb: light)
            }
        })
    }

    // Surfaces
    static let paper = Color.ink(0xfbfaf7, 0x16171a)
    static let card = Color.ink(0xffffff, 0x1e2024)
    static let sunk = Color.ink(0xf4f2ed, 0x17191c)
    static let rule = Color.ink(0xe4e0d7, 0x2e3238, contrast: (0xc1b7a2, 0x41474f))
    static let ruleStrong = Color.ink(0xcfc9bc, 0x414750, contrast: (0xa69c88, 0x5c6470))

    // Text. Every text colour is at least 4.5:1 on every surface above, in
    // both appearances: inkFaint and bucketOpen were darkened (and inkFaint
    // lightened at night) to get there, from 3.4 and 3.9.
    static let inkPrimary = Color.ink(0x1c1a17, 0xe9e7e3)
    static let inkSoft = Color.ink(0x5c574e, 0xa8a49d, contrast: (0x3e3a34, 0xcdcbc7))
    static let inkFaint = Color.ink(0x726c62, 0x8c8880, contrast: (0x555149, 0xadaaa4))

    // The three buckets, and the one alarm.
    static let bucketOpen = Color.ink(0x99600a, 0xe5a94f, contrast: (0x734807, 0xf0c078))
    static let bucketOpenBG = Color.ink(0xfdf3e2, 0x2e2617)
    static let bucketLearning = Color.ink(0x2f5fa8, 0x7fabe8, contrast: (0x234b87, 0xa3c4f0))
    static let bucketLearningBG = Color.ink(0xeaf1fb, 0x1a2433)
    static let bucketClosed = Color.ink(0x3d7a56, 0x72b98d, contrast: (0x2c5d41, 0x97d0ab))
    static let bucketClosedBG = Color.ink(0xe9f4ed, 0x18271e)
    static let alarm = Color.ink(0xa8352b, 0xe2867c, contrast: (0x86281f, 0xf0a9a1))
    static let alarmBG = Color.ink(0xfceceb, 0x2e1c1a)
}

private extension NSColor {
    convenience init(rgb: UInt32) {
        self.init(
            srgbRed: Double((rgb >> 16) & 0xff) / 255,
            green: Double((rgb >> 8) & 0xff) / 255,
            blue: Double(rgb & 0xff) / 255,
            alpha: 1
        )
    }
}

extension Bucket {
    var tint: Color {
        switch self {
        case .open: .bucketOpen
        case .learning: .bucketLearning
        case .closed: .bucketClosed
        default: .inkSoft
        }
    }

    /// The sidebar's symbol: a question waiting, a thing to read, a thing done.
    var symbol: String {
        switch self {
        case .open: "questionmark.circle"
        case .learning: "book"
        case .closed: "checkmark.circle"
        default: "circle"
        }
    }

    var wash: Color {
        switch self {
        case .open: .bucketOpenBG
        case .learning: .bucketLearningBG
        case .closed: .bucketClosedBG
        default: .sunk
        }
    }
}

extension BucketSection {
    /// Title case, as menu items are: "Waiting on You".
    var menuTitle: String {
        switch bucket {
        case .open: "Waiting on You"
        case .learning: "To Learn"
        default: title
        }
    }
}

extension SoloLevel {
    var label: String {
        switch self {
        case .isolated: "Isolated"
        case .listed: "Listed"
        case .causal: "Causal"
        default: rawValue.capitalized
        }
    }

    /// Only `causal` reads as arrival. The load-bearing boundary is
    /// `listed -> causal`: where reciting separates from understanding.
    var tint: Color {
        switch self {
        case .causal: .bucketClosed
        case .listed: .bucketLearning
        default: .bucketOpen
        }
    }
}

extension Font {
    /// New York, at wordmark and headline sizes.
    static func display(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .serif)
    }
}
