//  EmptyStateView.swift
//  UnrotMac
//
//  After the Silence artboard: silence has to read as a result.
//
//  Five states the server names, plus the one it cannot send. Each gets a pill
//  saying which it is, the server's own headline and detail, and the one action
//  it earns -- a button, never a terminal command. Only the failure is red.

import SwiftUI
import UnrotKit

struct EmptyStateView: View {
    struct Action {
        let title: String
        let primary: Bool
        let run: () -> Void
    }

    let state: SurfaceState
    let headline: String
    let detail: String
    var analysing: (done: Int, total: Int)?
    var action: Action?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Pip(text: label, tint: tint, wash: wash)
            Text(headline)
                .font(.display(28))
                .foregroundStyle(Color.inkPrimary)
            Text(detail)
                .font(.system(size: 13.5))
                .foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: 520, alignment: .leading)

            if let why {
                Text(why)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(12)
                    .frame(maxWidth: 520, alignment: .leading)
                    .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
            }

            if let analysing {
                ProgressView(value: Double(analysing.done), total: Double(max(analysing.total, 1))) {
                    Text("Examining \(analysing.done + 1) of \(analysing.total)…").font(.system(size: 12))
                }
                .frame(maxWidth: 360)
            } else if let action {
                Button(action.title, action: action.run)
                    .buttonStyle(UnrotButton(weight: action.primary ? .primary : .secondary))
                    .padding(.top, 4)
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.card, in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(state == .failed ? Color.alarm.opacity(0.45) : Color.rule, lineWidth: 1)
        )
    }

    private var label: String {
        switch state {
        case .clean: "Clean"
        case .coldStart: "Cold start"
        case .notCaptured: "Not captured"
        case .notAnalysed: "Not analysed"
        case .failed: "Failed"
        default: state.rawValue.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    /// The canvas's aside for the two states whose meaning is easiest to miss.
    private var why: String? {
        switch state {
        case .clean:
            "unrot records that it examined a session even when it flags nothing. Without that, a clean session and an unexamined one would look identical, and this sentence would be a guess."
        case .coldStart:
            "Nothing to do here. unrot would rather say it has seen too little than read a quiet week as a good one."
        default:
            nil
        }
    }

    /// Only the failure is red. Everything else is the product working.
    private var tint: Color {
        switch state {
        case .failed: .alarm
        case .clean: .bucketClosed
        case .notAnalysed: .bucketOpen
        default: .inkSoft
        }
    }

    private var wash: Color {
        switch state {
        case .failed: .alarmBG
        case .clean: .bucketClosedBG
        case .notAnalysed: .bucketOpenBG
        default: .sunk
        }
    }
}
