//  MaterialView.swift
//  UnrotMac
//
//  Both formats, and neither of them is the degraded one.
//
//  `sources_only` generates nothing, and so can invent nothing. That is a
//  property worth having rather than a fallback, so it is labelled as a format
//  and not as a failure.

import SwiftUI
import UnrotKit

struct MaterialView: View {
    let material: LearningMaterial

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Pip(text: label, tint: .inkSoft, wash: .sunk)
                Spacer()
                if material.deliveredAt != nil {
                    Text("delivered")
                        .font(.system(size: 10))
                        .foregroundStyle(Color.inkFaint)
                }
            }

            if let body = material.body, !body.isEmpty {
                Text(body)
                    .font(.system(size: 13))
                    .foregroundStyle(Color.inkPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }

            if !material.sources.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Sources")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Color.inkFaint)
                    ForEach(Array(material.sources.enumerated()), id: \.offset) { _, source in
                        SourceRow(source: source)
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
    }

    private var label: String {
        switch material.format {
        case .textual: "written, with sources"
        case .sourcesOnly: "sources only"
        default: material.format.rawValue
        }
    }
}

private struct SourceRow: View {
    let source: LearningMaterial.Source

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text("•").foregroundStyle(Color.inkFaint)
            if let urlText = source.url, let url = URL(string: urlText) {
                Link(source.title ?? urlText, destination: url)
                    .font(.system(size: 12))
            } else if let title = source.title {
                Text(title).font(.system(size: 12)).foregroundStyle(Color.inkSoft)
            }
            if let note = source.note {
                Text(note).font(.system(size: 11)).foregroundStyle(Color.inkFaint)
            }
        }
    }
}
