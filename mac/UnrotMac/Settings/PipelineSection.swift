//  PipelineSection.swift
//  UnrotMac
//
//  Dev-channel builds only: each analysis stage over the last week, from
//  `/api/pipeline` -- the same report `python -m unrot.store pipeline` prints.
//  Nothing is computed here: every count and rate arrives decided, and this
//  only lays them out.

#if DEV_FEATURES
import SwiftUI
import UnrotKit

struct PipelineSection: View {
    let client: UnrotClient

    @State private var report: PipelineReport?
    @State private var problem: String?
    @State private var loading = false

    var body: some View {
        Section {
            if let report {
                LabeledContent("Sessions examined", value: "\(report.sessions) · \(report.detectorCalls) detector calls")
                LabeledContent("Candidates found", value: "\(report.found) · \(report.gaps) waved through")
                LabeledContent("Triage",
                               value: "passed \(report.passed) · held back \(report.heldBack) · spot-checked \(report.spotChecks)")
                if report.notJudged > 0 {
                    LabeledContent("Not judged by triage", value: "\(report.notJudged)")
                }
                LabeledContent("Filed",
                               value: "\(report.filed) · confirmed \(report.confirmed) · dismissed \(report.dismissed) · unanswered \(report.unanswered)")
                LabeledContent("Flags that were real", value: ratio(report.flagPrecision))
                LabeledContent("Hold-backs that were right", value: ratio(report.holdBackPrecision))
            } else if let problem {
                Text(problem).font(.system(size: 11)).foregroundStyle(Color.inkSoft)
            } else {
                ProgressView().controlSize(.small)
            }
        } header: {
            HStack {
                Text("Pipeline, last 7 days")
                Spacer()
                Button(loading ? "Refreshing…" : "Refresh") { Task { await load() } }
                    .buttonStyle(.link)
                    .disabled(loading)
            }
        } footer: {
            Text("Hold-backs that were right comes only from spot checks: triage surfaces one in five of the terms it would have hidden, and asks. Under 90% over 10 answered, it stops holding anything back.")
                .font(.system(size: 11))
                .foregroundStyle(Color.inkFaint)
                .fixedSize(horizontal: false, vertical: true)
        }
        .task { await load() }

        if let report {
            Section {
                ForEach(PipelineReport.bands, id: \.self) { band in
                    LabeledContent(band, value: report.calibration[band].map(ratio) ?? "–")
                }
            } header: {
                Text("Dismissed, by triage's p(knows)")
            } footer: {
                Text("Among terms triage let through and you then answered. Should climb down the list; if it does not, the probability is noise.")
                    .font(.system(size: 11))
                    .foregroundStyle(Color.inkFaint)
            }

            Section("Per stage") {
                ForEach(PipelineReport.stageNames, id: \.0) { key, name in
                    if let stage = report.stages[key] {
                        LabeledContent(name, value: line(stage))
                    }
                }
            }
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            report = try await client.pipeline()
            problem = nil
        } catch {
            problem = "Could not read the pipeline report: \(error.localizedDescription)"
        }
    }

    private func ratio(_ value: PipelineRatio) -> String {
        guard let rate = value.rate, value.whole > 0 else { return "none to count" }
        let text = "\(value.part) of \(value.whole) (\(Int((rate * 100).rounded()))%)"
        return value.whole < 5 ? text + " · too few to read much into" : text
    }

    private func line(_ stage: StageCost) -> String {
        var parts = ["\(stage.calls) calls"]
        if stage.failed > 0 { parts.append("\(stage.failed) failed") }
        if let ms = stage.medianMs { parts.append(String(format: "median %.1fs", ms / 1000)) }
        parts.append(stage.costText)
        if stage.unpriced > 0 { parts.append("+ \(stage.unpriced) unpriced") }
        return parts.joined(separator: " · ")
    }
}
#endif
