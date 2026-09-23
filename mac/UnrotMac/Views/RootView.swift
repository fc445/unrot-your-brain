//  RootView.swift
//  UnrotMac
//
//  The window, after the Main and Silence artboards: a title bar carrying the
//  wordmark and the one ambient status, a sidebar of the three lists, and the
//  page.
//
//  Nothing here decides a bucket, a surface state, a headline or a next step --
//  those arrive from /api/surface. What this file decides is layout, focus, and
//  which of the server's states gets which button.

import SwiftUI
import UnrotKit

struct RootView: View {
    let core: CoreProcess
    @Bindable var store: SurfaceStore
    let quick: QuickAccept
    let watcher: Watcher
    var router: Router
    var addGap: () -> Void = {}
    /// Present until the first run is finished; nothing is captured before then.
    var onboarding: Onboarding? = nil
    var modelSettings: ModelSettings? = nil

    /// The card K and D answer. Arrow keys move it; it defaults to the top.
    @State private var focused: String?
    @Environment(\.undoManager) private var undoManager

    private var waiting: [Concept] { store.concepts(in: .open) }
    /// The watcher does not start until the first run is finished, so the bar must not
    /// claim it is watching while the user is still being asked whether it may.
    private var settingUp: Bool { onboarding.map { !$0.done } ?? false }
    private var focusedConcept: Concept? {
        waiting.first { $0.conceptId == focused } ?? waiting.first
    }

    var body: some View {
        VStack(spacing: 0) {
            TitleBar(watcher: watcher, core: core, settingUp: settingUp, addGap: addGap)
            Divider()
            if let onboarding, settingUp, let modelSettings {
                FirstRunView(onboarding: onboarding, watcher: watcher, settings: modelSettings)
            } else {
                HStack(spacing: 0) {
                    Sidebar(store: store, core: core)
                    Divider()
                    page
                }
            }
        }
        .background(Color.paper)
        .task {
            await store.load()
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard core.status.isUp else { continue }
                await store.load()
            }
        }
        // K and D answer the focused card; the arrows move focus. Handled here
        // rather than as keyboard shortcuts, because a shortcut on a bare
        // letter fires even while a text editor has focus.
        .focusable()
        .focusEffectDisabled()
        .onKeyPress(keys: [.upArrow, .downArrow]) { press in
            move(press.key == .upArrow ? -1 : 1)
            return .handled
        }
        .onKeyPress(characters: CharacterSet(charactersIn: "dDkK"), phases: .down) { press in
            guard let concept = focusedConcept, let encounter = concept.unanswered else { return .ignored }
            let verdict: Verdict = press.characters.lowercased() == "d" ? .confirm : .dismiss
            Task { await quick.answer(concept: concept, encounter: encounter, verdict) }
            return .handled
        }
        .overlay(alignment: .bottom) {
            if let answer = quick.undoable {
                UndoStrip(answer: answer) { Task { await quick.undo() } }
                    .frame(maxWidth: 440)
                    .shadow(color: .black.opacity(0.1), radius: 10, y: 3)
                    .padding(.bottom, 18)
            }
        }
        .onChange(of: quick.undoable) { _, answer in
            undoManager?.removeAllActions(withTarget: quick)
            guard let answer else { return }
            undoManager?.registerUndo(withTarget: quick) { target in
                Task { @MainActor in await target.undo() }
            }
            undoManager?.setActionName(answer.verdict == .confirm ? "“Didn't Know This”" : "“Knew It”")
        }
        .sheet(item: Binding(get: { router.moment }, set: { router.moment = $0 })) { request in
            MomentSheet(request: request, store: store, quick: quick) { router.moment = nil }
        }
        .sheet(item: Binding(get: { router.check }, set: { router.check = $0 })) { request in
            CheckSheet(conceptId: request.conceptId, store: store) { router.check = nil }
        }
    }

    private func move(_ step: Int) {
        guard !waiting.isEmpty else { return }
        let index = waiting.firstIndex { $0.conceptId == focusedConcept?.conceptId } ?? 0
        focused = waiting[max(0, min(waiting.count - 1, index + step))].conceptId
    }

    // MARK: - The page

    private var page: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if let surface = store.surface, store.state != .failed {
                    Text(meta(surface))
                        .font(.system(size: 12))
                        .foregroundStyle(Color.inkFaint)
                        .padding(.bottom, 6)
                }

                if let empty = store.emptyState {
                    EmptyStateView(
                        state: empty,
                        headline: empty == .failed ? "unrot-core isn't running" : store.surface?.headline ?? "",
                        detail: empty == .failed
                            ? "The window can't reach the local core. This is not an empty list — it is an unanswered question."
                            : store.surface?.detail ?? "",
                        analysing: watcher.progress,
                        action: action(for: empty)
                    )
                } else if let surface = store.surface {
                    Text(surface.headline)
                        .font(.display(32))
                        .foregroundStyle(Color.inkPrimary)
                    Text(surface.detail)
                        .font(.system(size: 13.5))
                        .foregroundStyle(Color.inkSoft)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, 4)

                    QueueBar(watcher: watcher).padding(.top, 14)

                    if surface.fixtures > 0 {
                        FixturesNotice(count: surface.fixtures).padding(.top, 14)
                    }

                    ForEach(store.populated) { section in
                        SectionHeader(section: section, count: surface.count(section.bucket))
                            .padding(.top, 26)
                            .padding(.bottom, 10)
                            .id(section.bucket.rawValue)
                        if section.bucket == .closed {
                            ClosedChips(concepts: store.concepts(in: .closed))
                        } else {
                            VStack(spacing: 10) {
                                ForEach(store.concepts(in: section.bucket)) { concept in
                                    GapCardView(
                                        concept: concept,
                                        store: store,
                                        quick: quick,
                                        router: router,
                                        isFocused: concept.conceptId == focusedConcept?.conceptId
                                    )
                                    .onTapGesture { if section.bucket == .open { focused = concept.conceptId } }
                                }
                            }
                        }
                    }
                } else if !store.hasLoadedOnce {
                    Text(core.status.isUp ? "Reading the log…" : "Starting the core…")
                        .font(.system(size: 13))
                        .foregroundStyle(Color.inkFaint)
                        .padding(.top, 40)
                }
            }
            .padding(.horizontal, 28)
            .padding(.top, 24)
            .padding(.bottom, 64)
            .frame(maxWidth: 820, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// "31 of 34 sessions examined · last activity Thursday 19:42".
    private func meta(_ surface: Surface) -> String {
        var line = "\(surface.capture.sessionsAnalysed) of \(surface.capture.sessions) sessions examined"
        if let last = Dates.recent(surface.capture.lastActivity) { line += " · last activity \(last)" }
        return line
    }

    /// The one button each empty state earns, if any. Cold start deliberately
    /// has none: admitting there is too little history is the point.
    private func action(for state: SurfaceState) -> EmptyStateView.Action? {
        switch state {
        case .failed:
            return .init(title: "Restart the core", primary: false) { core.restart() }
        case .notAnalysed where watcher.canAnalyse && !watcher.isRunning:
            // What it will roughly cost, beside the button that spends it.
            let title = watcher.estimate?.text.map { "Examine them now · \($0)" } ?? "Examine them now"
            return .init(title: title, primary: true) { watcher.analyseNow() }
        case .notCaptured where !watcher.paused:
            return .init(title: "Look for sessions now", primary: false) { Task { await watcher.sweep() } }
        default:
            return nil
        }
    }
}

// MARK: - Title bar

private struct TitleBar: View {
    let watcher: Watcher
    let core: CoreProcess
    let settingUp: Bool
    let addGap: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            // Room for the traffic lights, which sit in this bar.
            Spacer().frame(width: 64)
            Text("unrot").font(.display(19)).foregroundStyle(Color.inkPrimary)
            Spacer()
            if !settingUp {
                StatusPill(watcher: watcher, core: core)
                Button(action: addGap) {
                    Label("Add a gap", systemImage: "plus")
                }
                .buttonStyle(UnrotButton())
                .keyboardShortcut("n")
            }
        }
        .padding(.horizontal, 14)
        .frame(height: 52)
        .background(Color.sunk)
    }
}

// MARK: - Sidebar

private struct Sidebar: View {
    let store: SurfaceStore
    let core: CoreProcess

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Eyebrow(text: "Lists").padding(.horizontal, 10).padding(.bottom, 4)
            ForEach(BucketSection.all) { section in
                HStack(spacing: 9) {
                    Circle().fill(section.bucket.tint).frame(width: 8, height: 8)
                    Text(section.title)
                        .font(.system(size: 13, weight: store.surface?.count(section.bucket) ?? 0 > 0 && section.bucket == .open ? .semibold : .regular))
                    Spacer()
                    Text("\(store.surface?.count(section.bucket) ?? 0)")
                        .font(.system(size: 12).monospacedDigit())
                        .foregroundStyle(Color.inkFaint)
                }
                .foregroundStyle(Color.inkPrimary)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(
                    RoundedRectangle(cornerRadius: 6)
                        .fill(section.bucket == .open && (store.surface?.count(.open) ?? 0) > 0 ? Color.rule.opacity(0.7) : .clear)
                )
            }
            Spacer()
            Divider().padding(.bottom, 6)
            HStack(spacing: 6) {
                Circle().fill(core.status.isUp ? Color.watching : Color.alarm).frame(width: 6, height: 6)
                Text(core.status.isUp ? "core running · local socket" : "core not running")
                    .font(.system(size: 11))
                    .foregroundStyle(core.status.isUp ? Color.inkFaint : Color.alarm)
            }
            .padding(.horizontal, 10)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 18)
        .frame(width: 200)
        .background(Color.sunk)
    }
}

// MARK: - Sections

private struct SectionHeader: View {
    let section: BucketSection
    let count: Int

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(section.title)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Color.inkPrimary)
            Text("\(count)")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(section.bucket.tint)
                .padding(.horizontal, 6)
                .padding(.vertical, 1)
                .background(section.bucket.wash, in: Capsule())
            Text(section.note)
                .font(.system(size: 12))
                .foregroundStyle(Color.inkFaint)
        }
    }
}

/// Closed is progress, not a list to work through: names, not cards.
private struct ClosedChips: View {
    let concepts: [Concept]
    private let shown = 12

    var body: some View {
        FlowLayout(spacing: 8) {
            ForEach(concepts.prefix(shown)) { concept in
                Text(concept.name)
                    .font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(Color.bucketClosed)
                    .padding(.horizontal, 11)
                    .padding(.vertical, 5)
                    .background(Color.bucketClosedBG, in: Capsule())
                    .help(concept.lead?.paraphrase ?? concept.name)
            }
            if concepts.count > shown {
                Text("and \(concepts.count - shown) more")
                    .font(.system(size: 12.5))
                    .foregroundStyle(Color.inkFaint)
                    .padding(.vertical, 5)
            }
        }
    }
}

/// Wraps its children onto as many rows as they need.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        let height = rows.map(\.height).reduce(0, +) + spacing * CGFloat(max(0, rows.count - 1))
        let width = rows.map(\.width).max() ?? 0
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for index in row.items {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row { var items: [Int] = []; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
        var rows = [Row()]
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            if rows[rows.count - 1].width + size.width > width, !rows[rows.count - 1].items.isEmpty {
                rows.append(Row())
            }
            let gap = rows[rows.count - 1].items.isEmpty ? 0 : spacing
            rows[rows.count - 1].items.append(index)
            rows[rows.count - 1].width += gap + size.width
            rows[rows.count - 1].height = max(rows[rows.count - 1].height, size.height)
        }
        return rows
    }
}

// MARK: - Bars and notices

/// Captured sessions waiting to be analysed, the button that spends money on
/// them, and what has been spent. Absent when there is nothing to say.
private struct QueueBar: View {
    let watcher: Watcher

    var body: some View {
        if let line {
            HStack(spacing: 10) {
                Image(systemName: icon).foregroundStyle(Color.inkFaint)
                Text(line)
                    .foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if let spent {
                    Text(spent)
                        .foregroundStyle(Color.inkFaint)
                        .monospacedDigit()
                        .lineLimit(1)
                }
                if watcher.isRunning {
                    Button("Stop") { watcher.stopAnalysing() }.buttonStyle(UnrotButton())
                } else if watcher.paused {
                    Button("Resume") { watcher.paused = false }.buttonStyle(UnrotButton())
                } else if watcher.pendingCount > 0 && watcher.canAnalyse {
                    Button("Analyse now") { watcher.analyseNow() }.buttonStyle(UnrotButton(weight: .primary))
                }
            }
            .font(.system(size: 12.5))
            .padding(10)
            .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
        }
    }

    private var line: String? {
        if let error = watcher.lastError { return error }
        if let progress = watcher.progress { return "Analysing \(progress.done + 1) of \(progress.total)…" }
        if watcher.paused { return "Watching is paused. Finished sessions are not being captured." }
        let n = watcher.pendingCount
        guard n > 0 else {
            // Nothing waiting, but money went out this week: still worth a line.
            return spent == nil ? nil : "Nothing waiting to be analysed."
        }
        let sessions = n == 1 ? "1 captured session is" : "\(n) captured sessions are"
        if !watcher.canAnalyse {
            return "\(sessions) waiting, but no model is configured. Add one in Settings › Model."
        }
        let waiting = watcher.autoAnalyse
            ? "\(sessions) waiting from before automatic analysis was on"
            : "\(sessions) waiting to be analysed"
        // "about $0.12" or "local, no cost" -- worded by the core, from recent
        // sessions on the same model. Nothing to go on, nothing said.
        return waiting + (watcher.estimate?.text.map { " — \($0)." } ?? ".")
    }

    /// The running total while a batch runs or right after one failed -- the
    /// case that matters, since a failed call is still billed -- and otherwise
    /// the week so far.
    private var spent: String? {
        if watcher.isRunning || watcher.lastError != nil,
           let batch = watcher.batchSpent, batch.calls > 0 {
            return "This run: \(batch.text)"
        }
        if let week = watcher.spentThisWeek, week.calls > 0 {
            return "This week: \(week.text)"
        }
        return nil
    }

    private var icon: String {
        if watcher.lastError != nil { return "exclamationmark.circle" }
        if watcher.paused { return "pause.circle" }
        return watcher.pendingCount == 0 && !watcher.isRunning ? "tray" : "tray.full"
    }
}

/// Seeded data must never read as a finding about the user.
private struct FixturesNotice: View {
    let count: Int

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Pip(text: "fixtures", tint: .inkFaint, wash: .rule)
            Text("\(count) of these events are development fixtures. Remove them with `python -m unrot.store seed --clear`.")
                .font(.system(size: 12))
                .foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
    }
}
