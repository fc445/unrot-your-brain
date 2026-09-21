//  EmptyStateView.swift
//  UnrotMac
//
//  Six answers, five of which are calm.
//
//  Journey 3 is the reason this is a view and not a `Text("No results")`. "We
//  looked and found nothing" has to be legible as a *good* result, and
//  distinguishable from capture never running, from analysis never running,
//  from too little history, and from the core being down. One empty list
//  cannot say five things, so the server names which it is and this renders
//  whichever it is told.
//
//  Only `failed` is red. A clean week is not a warning.

import SwiftUI
import UnrotKit

struct EmptyStateView: View {
    let state: SurfaceState
    let headline: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: symbol)
                    .font(.system(size: 15, weight: .medium))
                    .foregroundStyle(tint)
                Text(headline)
                    .font(.display(24))
                    .foregroundStyle(Color.inkPrimary)
            }

            Text(detail)
                .font(.system(size: 13))
                .foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)

            if let step = nextStep {
                Text(step)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Color.inkSoft)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
                    .textSelection(.enabled)
            }
        }
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(wash, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.rule, lineWidth: 1))
        .padding(.top, 20)
    }

    private var symbol: String {
        switch state {
        case .clean: "checkmark.seal"
        case .coldStart: "hourglass"
        case .notCaptured: "tray"
        case .notAnalysed: "questionmark.folder"
        case .failed: "exclamationmark.triangle"
        default: "circle"
        }
    }

    /// The only red on the page, and only for the one state the server cannot
    /// send. Everything else is the product working.
    private var tint: Color { state == .failed ? .alarm : .inkSoft }
    private var wash: Color { state == .failed ? .alarmBG : .card }

    /// A command, where there is one worth running. The wording of *why*
    /// belongs to the server; this is only the thing to type next.
    private var nextStep: String? {
        switch state {
        case .notCaptured: "python -m unrot.capture ingest"
        case .notAnalysed: "python -m unrot.resolver run"
        case .failed: "python -m unrot.api --uds"
        default: nil
        }
    }
}
