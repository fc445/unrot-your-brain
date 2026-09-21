//  MomentView.swift
//  UnrotMac
//
//  The point of acceptance, replayed rather than abstracted into a flag.
//
//  This is S4's local layer: the paraphrase is what travels between machines,
//  and this is what only exists on the one that produced it. It resolves
//  against unrot's own retained copy, not `~/.claude`, which may have rotated
//  the original away.
//
//  There are three distinct reasons it can be unavailable, and all three are
//  honest answers rather than errors. Collapsing them into one "not available"
//  would throw away the only thing that tells you which.

import SwiftUI
import UnrotKit

struct MomentView: View {
    let moment: Moment

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if moment.resolvable {
                if let session = moment.sessionId {
                    Text("\(session) · lines \(moment.lineStart ?? 0)–\(moment.lineEnd ?? 0)")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(Color.inkFaint)
                }
                ForEach(moment.turns) { turn in
                    TurnRow(turn: turn)
                }
            } else {
                Text(moment.reason ?? "This moment cannot be replayed on this machine.")
                    .font(.system(size: 12))
                    .foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(14)
        .frame(minWidth: 420, alignment: .leading)
    }
}

private struct TurnRow: View {
    let turn: MomentTurn

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(turn.role)
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(turn.role == "user" ? Color.bucketOpen : Color.inkFaint)
                // The server marks these, and the view shows them, because a
                // hook's injected text is not something the user said and must
                // never be presented as though it were.
                if turn.isMeta { Pip(text: "injected", tint: .inkFaint, wash: .sunk) }
                if turn.isSidechain { Pip(text: "sidechain", tint: .inkFaint, wash: .sunk) }
                Spacer()
                Text("\(turn.lineNo)")
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(Color.inkFaint)
            }
            Text(turn.text)
                .font(.system(size: 12))
                .foregroundStyle(Color.inkPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(turn.role == "user" ? Color.bucketOpenBG : Color.sunk,
                    in: RoundedRectangle(cornerRadius: 6))
    }
}
