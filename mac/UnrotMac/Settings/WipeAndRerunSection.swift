//  WipeAndRerunSection.swift
//  UnrotMac
//
//  Dev-channel builds only: discard everything the models generated from
//  transcripts and start detection over. Built on top of `Regenerator` rather
//  than beside it -- a wipe only clears `session_analysed` and the derived data
//  hanging off it (see `regen/wipe.py`), so once it is done every captured
//  session is due a re-run, and `Regenerator` already knows how to drive
//  exactly that: one session at a time, stoppable, with progress and cost
//  reporting. This section starts it; the progress bar and Stop button below
//  are the same `Regenerator` the Advanced tab's own pane uses.

#if DEV_FEATURES
import AppKit
import SwiftUI
import UnrotKit

struct WipeAndRerunSection: View {
    let client: UnrotClient
    let regenerator: Regenerator

    @State private var plan: DevWipePlan?
    @State private var problem: String?
    @State private var discardUserInput = false
    @State private var confirming = false
    @State private var wiping = false
    @State private var lastResult: DevWipeResult?

    var body: some View {
        Section {
            Text("Discards every detected encounter, resolver judgment, grade and generated material, and re-examines all captured sessions under the model in effect now, from nothing. For a store full of half-finished experiments, not for a bad answer here and there -- for that, Advanced's Regenerate leaves everything alone except what it re-examines.")
                .font(.system(size: 12))
                .fixedSize(horizontal: false, vertical: true)

            Toggle("Also discard judgments, explanations and manual submissions", isOn: $discardUserInput)
                .disabled(wiping || regenerator.isRunning)
            Text(discardUserInput
                 ? "A completely empty store: nothing you confirmed, dismissed, explained or typed in by hand survives either. Only the raw captured transcripts are left to rebuild from."
                 : "Off (default): anything you confirmed, dismissed, explained or submitted by hand is left exactly as it is.")
                .font(.system(size: 11))
                .foregroundStyle(discardUserInput ? Color.alarm : Color.inkFaint)
                .fixedSize(horizontal: false, vertical: true)

            if let plan {
                LabeledContent("Sessions to re-run", value: "\(plan.sessionsToRerun)")
                LabeledContent("Judged encounters", value: countLine(plan.protectedEncounters))
                LabeledContent("Manual submissions", value: countLine(plan.manualEncounters))
                LabeledContent("Explanations", value: countLine(plan.explanations))
            } else if let problem {
                Text(problem).font(.system(size: 11)).foregroundStyle(Color.inkSoft)
            }
        } header: {
            Text("Wipe and re-run")
        }

        Section {
            if let progress = regenerator.progress {
                ProgressView(value: Double(progress.done), total: Double(max(progress.total, 1))) {
                    Text("Re-examined \(progress.done) of \(progress.total)")
                }
                Button("Stop") { regenerator.stop() }
            } else {
                Button(wiping ? "Wiping…" : "Wipe and re-run…") { confirming = true }
                    .disabled(wiping || (plan?.sessionsToRerun ?? 0) == 0 || plan?.canRun != true)
                if plan?.canRun == false {
                    Text("No model is configured, so nothing can be re-examined afterward.")
                        .font(.system(size: 11)).foregroundStyle(Color.inkFaint)
                }
            }
            if let lastResult {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Backed up to \(lastResult.backupPath)")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(Color.inkFaint)
                        .textSelection(.enabled)
                    Button("Reveal backup in Finder") {
                        NSWorkspace.shared.activateFileViewerSelecting(
                            [URL(fileURLWithPath: lastResult.backupPath)]
                        )
                    }
                }
            }
        }
        .task { await refresh() }
        .onChange(of: regenerator.isRunning) { _, running in
            guard !running else { return }
            Task { await refresh() }
        }
        .confirmationDialog(
            "Wipe and re-run \(plan?.sessionsToRerun ?? 0) session\((plan?.sessionsToRerun ?? 0) == 1 ? "" : "s")?",
            isPresented: $confirming
        ) {
            Button(discardUserInput ? "Wipe everything" : "Wipe and re-run", role: .destructive) {
                Task { await wipeAndRerun() }
            }
        } message: {
            Text(confirmationMessage)
        }
    }

    private func countLine(_ n: Int) -> String {
        discardUserInput ? "\(n) — discarded" : "\(n) — kept"
    }

    private var confirmationMessage: String {
        let n = plan?.sessionsToRerun ?? 0
        let cost = "A backup of the store is made first. This re-examines \(n)"
            + " session\(n == 1 ? "" : "s") under the model in effect now, which makes"
            + " a model call for each one and costs what that costs."
        guard discardUserInput else {
            return "This discards every detected encounter, resolver judgment, grade and"
                + " generated material. Your judgments, explanations and anything you"
                + " submitted by hand are left alone. \(cost)"
        }
        return "This discards EVERYTHING recorded so far — judgments, explanations, manual"
            + " submissions and concept names included — leaving only the raw captured"
            + " transcripts. \(cost)"
    }

    private func refresh() async {
        do {
            plan = try await client.devWipePlan()
            problem = nil
        } catch let error as APIError {
            plan = nil
            problem = error.message
        } catch {
            plan = nil
            problem = "Could not read what a wipe would do."
        }
    }

    private func wipeAndRerun() async {
        wiping = true
        problem = nil
        do {
            lastResult = try await client.devWipe(discardUserInput: discardUserInput)
            wiping = false
            await regenerator.refresh()
            regenerator.start()
        } catch let error as APIError {
            problem = error.message
            wiping = false
        } catch {
            problem = "The wipe did not complete."
            wiping = false
        }
        await refresh()
    }
}
#endif
