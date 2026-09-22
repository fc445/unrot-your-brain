//  MaterialView.swift
//  UnrotMac
//
//  After the Material artboard: material is opt-in, and it is grounded.
//
//  Two formats, and neither is the degraded one. Sources only generates
//  nothing, so it can invent nothing; written material marks every claim with
//  the source it stands on.

import SwiftUI
import UnrotKit

struct MaterialView: View {
    let material: LearningMaterial

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text(material.format == .sourcesOnly ? "Sources only" : "Written, with sources")
                    .font(.system(size: 13, weight: .semibold))
                Pip(
                    text: material.format == .sourcesOnly ? "generates nothing" : "every claim marked",
                    tint: material.format == .sourcesOnly ? .bucketClosed : .bucketLearning,
                    wash: material.format == .sourcesOnly ? .bucketClosedBG : .bucketLearningBG
                )
                Spacer()
            }

            if let body = material.body, !body.isEmpty {
                Text(Self.cited(body))
                    .font(.system(size: 13.5))
                    .foregroundStyle(Color.inkPrimary)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }

            if !material.sources.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    if material.format != .sourcesOnly { Eyebrow(text: "Stands on") }
                    ForEach(Array(material.sources.enumerated()), id: \.offset) { index, source in
                        SourceRow(number: index + 1, source: source)
                    }
                }
                .padding(.top, 2)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
    }

    /// "[S1]" and "[S1,S2]" become superscript numbers in the link colour, so
    /// the prose reads as prose and the grounding is still on every claim.
    static func cited(_ body: String) -> AttributedString {
        var out = AttributedString()
        var rest = Substring(body)
        while let open = rest.firstIndex(of: "["), let close = rest[open...].firstIndex(of: "]") {
            let inside = rest[rest.index(after: open)..<close]
            let marks = inside.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
            guard !marks.isEmpty, marks.allSatisfy({ $0.hasPrefix("S") && Int($0.dropFirst()) != nil }) else {
                out += AttributedString(rest[..<rest.index(after: open)])
                rest = rest[rest.index(after: open)...]
                continue
            }
            out += AttributedString(rest[..<open])
            var mark = AttributedString(marks.map { String($0.dropFirst()) }.joined(separator: ","))
            mark.baselineOffset = 5
            mark.font = .system(size: 9.5, weight: .semibold)
            mark.foregroundColor = Color.bucketLearning
            out += mark
            rest = rest[rest.index(after: close)...]
        }
        out += AttributedString(rest)
        return out
    }
}

private struct SourceRow: View {
    let number: Int
    let source: LearningMaterial.Source

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\(number)")
                .font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                .foregroundStyle(Color.inkFaint)
                .frame(width: 14, alignment: .trailing)
            VStack(alignment: .leading, spacing: 2) {
                if let url = source.url {
                    Link(source.title ?? url.absoluteString, destination: url)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Color.link)
                } else {
                    Text(source.title ?? source.ref ?? "Untitled source")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Color.inkPrimary)
                }
                if let whereFrom = source.whereFrom {
                    Text(whereFrom)
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(Color.inkFaint)
                }
                if let excerpt = source.excerpt, !excerpt.isEmpty {
                    Text(excerpt)
                        .font(.system(size: 12))
                        .foregroundStyle(Color.inkSoft)
                        .lineLimit(3)
                }
                if source.verified == false, let note = source.note {
                    Text(note).font(.system(size: 11)).italic().foregroundStyle(Color.inkFaint)
                }
            }
        }
    }
}
