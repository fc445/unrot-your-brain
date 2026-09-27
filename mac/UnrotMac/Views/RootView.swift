//  RootView.swift
//  UnrotMac
//
//  The window: a sidebar of the three lists, and a page for whichever one is
//  selected. Both are the system's -- a `NavigationSplitView`, a sidebar
//  `List`, and a toolbar in the window frame -- so hiding the sidebar, the
//  window's active and inactive looks, and the View menu's Show Sidebar are
//  the Mac's rather than ours to keep in step.
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
    @Bindable var router: Router
    var addGap: () -> Void = {}
    /// Present until the first run is finished; nothing is captured before then.
    var onboarding: Onboarding? = nil
    var modelSettings: ModelSettings? = nil

    /// The card K and D answer. Arrow keys move it; it defaults to the top.
    @State private var focused: String?
    /// Whether the page, rather than the sidebar, has the keyboard. K and D
    /// only mean something to the page, so it starts with it.
    @FocusState private var pageHasKeyboard: Bool
    @Environment(\.undoManager) private var undoManager

    private var waiting: [Concept] { store.concepts(in: .open) }
    /// The watcher does not start until the first run is finished, so the bar must not
    /// claim it is watching while the user is still being asked whether it may.
    private var settingUp: Bool { onboarding.map { !$0.done } ?? false }
    private var focusedConcept: Concept? {
        waiting.first { $0.conceptId == focused } ?? waiting.first
    }
    private var list: BucketSection {
        BucketSection.all.first { $0.bucket == router.list } ?? BucketSection.all[0]
    }

    var body: some View {
        Group {
            if let onboarding, settingUp, let modelSettings {
                FirstRunView(onboarding: onboarding, watcher: watcher, settings: modelSettings)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .background(Color.paper)
                    .navigationTitle("Welcome to unrot")
            } else {
                NavigationSplitView {
                    Sidebar(store: store, selection: $router.list)
                        .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 280)
                } detail: {
                    page
                        .navigationSubtitle(meta ?? "")
                        .toolbar { toolbar }
                }
                .navigationTitle(list.title)
            }
        }
        .task {
            await store.load()
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard core.status.isUp else { continue }
                await store.load()
            }
        }
        // Follows the last answer rather than the strip, so Edit › Undo
        // outlives the strip's eight seconds.
        .onChange(of: quick.lastAnswer) { _, answer in
            undoManager?.removeAllActions(withTarget: quick)
            guard let answer else { return }
            undoManager?.registerUndo(withTarget: quick) { target in
                Task { @MainActor in await target.undo() }
            }
            undoManager?.setActionName(answer.verdict == .confirm ? "“Didn't Know This”" : "“Knew It”")
        }
        .sheet(item: $router.moment) { request in
            MomentSheet(request: request, store: store, quick: quick) { router.moment = nil }
        }
        .sheet(item: $router.check) { request in
            CheckSheet(conceptId: request.conceptId, store: store) { router.check = nil }
        }
    }

    // MARK: - Toolbar

    /// The one ambient status and the one action. Adding a gap is also File ›
    /// Add a Gap… (⌘N), which is where its shortcut lives: the toolbar can be
    /// hidden, the menu bar cannot.
    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .status) {
            StatusPill(watcher: watcher, core: core, bare: true)
        }
        ToolbarItem(placement: .primaryAction) {
            Button(action: addGap) {
                Label("Add a Gap…", systemImage: "plus")
            }
            .help("Add a gap you noticed yourself")
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
                if let empty = store.emptyState {
                    // Every list is empty, or the core is unreachable: a fact
                    // about the whole app, so it reads the same on every list.
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
                    header(surface)
                    if surface.fixtures > 0 {
                        FixturesNotice(count: surface.fixtures).padding(.top, 14)
                    }
                    contents.padding(.top, 22)
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
        .background(Color.paper)
        .overlay(alignment: .bottom) {
            if let answer = quick.undoable {
                UndoStrip(answer: answer) { Task { await quick.undo() } }
                    .frame(maxWidth: 440)
                    .shadow(color: .black.opacity(0.1), radius: 10, y: 3)
                    .padding(.bottom, 18)
            }
        }
        // K and D answer the focused card; the arrows move focus. Handled here
        // rather than as keyboard shortcuts, because a shortcut on a bare
        // letter fires even while a text editor has focus. Only Waiting on you
        // has cards to answer, so everywhere else the keys fall through.
        .focusable()
        .focusEffectDisabled()
        .focused($pageHasKeyboard)
        .onAppear { pageHasKeyboard = true }
        .onKeyPress(keys: [.upArrow, .downArrow]) { press in
            guard list.bucket == .open else { return .ignored }
            move(press.key == .upArrow ? -1 : 1)
            return .handled
        }
        .onKeyPress(characters: CharacterSet(charactersIn: "dDkK"), phases: .down) { press in
            guard list.bucket == .open,
                  let concept = focusedConcept, let encounter = concept.unanswered else { return .ignored }
            let verdict: Verdict = press.characters.lowercased() == "d" ? .confirm : .dismiss
            Task { await quick.answer(concept: concept, encounter: encounter, verdict) }
            return .handled
        }
    }

    /// Waiting on you keeps the server's headline, which is written for it;
    /// the other two lists say what they hold.
    @ViewBuilder
    private func header(_ surface: Surface) -> some View {
        Text(list.bucket == .open ? surface.headline : list.title)
            .font(.display(32))
            .foregroundStyle(Color.inkPrimary)
        Text(list.bucket == .open ? surface.detail : list.note + ".")
            .font(.system(size: 13.5))
            .foregroundStyle(Color.inkSoft)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 4)
        if list.bucket == .open {
            QueueBar(watcher: watcher).padding(.top, 14)
        }
    }

    @ViewBuilder
    private var contents: some View {
        let concepts = store.concepts(in: list.bucket)
        if concepts.isEmpty {
            // Waiting on you's headline already says it is empty.
            if list.bucket != .open { EmptyList(bucket: list.bucket) }
        } else if list.bucket == .closed {
            ClosedChips(concepts: concepts)
        } else {
            VStack(spacing: 10) {
                ForEach(concepts) { concept in
                    GapCardView(
                        concept: concept,
                        store: store,
                        quick: quick,
                        router: router,
                        isFocused: list.bucket == .open && concept.conceptId == focusedConcept?.conceptId
                    )
                    .onTapGesture {
                        guard list.bucket == .open else { return }
                        focused = concept.conceptId
                        pageHasKeyboard = true
                    }
                }
            }
        }
    }

    /// "31 of 34 sessions examined · last activity Thursday 19:42", under the
    /// window title. Nothing when there is nothing true to say.
    private var meta: String? {
        guard let surface = store.surface, store.state != .failed else { return nil }
        let capture = surface.capture
        var line = "\(capture.sessionsAnalysed) of \(capture.sessionsAnalysable ?? capture.sessions) sessions examined"
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

// MARK: - Sidebar

private struct Sidebar: View {
    let store: SurfaceStore
    @Binding var selection: Bucket?

    var body: some View {
        List(selection: $selection) {
            ForEach(BucketSection.all) { section in
                SidebarRow(section: section, count: store.surface?.count(section.bucket) ?? 0)
                    .tag(section.bucket)
            }
        }
        .listStyle(.sidebar)
    }
}

private struct SidebarRow: View {
    let section: BucketSection
    let count: Int
    /// Increased on the selected row of the focused list, whose highlight is
    /// the accent colour: a bucket tint on top of it would fight it.
    @Environment(\.backgroundProminence) private var prominence

    var body: some View {
        Label {
            Text(section.title)
        } icon: {
            Image(systemName: section.bucket.symbol)
                .foregroundStyle(prominence == .increased ? AnyShapeStyle(.white) : AnyShapeStyle(section.bucket.tint))
        }
        .badge(count)
    }
}

// MARK: - Lists

/// A list that is empty while another is not. The whole-app empty states are
/// EmptyStateView's; this only says what would put something here.
private struct EmptyList: View {
    let bucket: Bucket

    var body: some View {
        ContentUnavailableView {
            Label(bucket == .learning ? "Nothing to learn" : "Nothing closed yet", systemImage: bucket.symbol)
        } description: {
            Text(bucket == .learning
                 ? "When you say you didn't know something waiting on you, it comes here."
                 : "A gap closes when you knew it, or when you explain it well enough to say why it works.")
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 40)
    }
}

/// Closed is progress, not a list to work through: names, not cards.
private struct ClosedChips: View {
    let concepts: [Concept]

    var body: some View {
        FlowLayout(spacing: 8) {
            ForEach(concepts) { concept in
                Text(concept.name)
                    .font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(Color.bucketClosed)
                    .padding(.horizontal, 11)
                    .padding(.vertical, 5)
                    .background(Color.bucketClosedBG, in: Capsule())
                    .help(concept.lead?.paraphrase ?? concept.name)
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

/// Captured sessions waiting to be analysed, and the button that spends money
/// on them. Absent when there is nothing to say.
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
        guard n > 0 else { return nil }
        let sessions = n == 1 ? "1 captured session is" : "\(n) captured sessions are"
        if !watcher.canAnalyse {
            return "\(sessions) waiting, but no model is configured. Add one in Settings › Model."
        }
        return watcher.autoAnalyse
            ? "\(sessions) waiting from before automatic analysis was on."
            : "\(sessions) waiting to be analysed."
    }

    private var icon: String {
        if watcher.lastError != nil { return "exclamationmark.circle" }
        if watcher.paused { return "pause.circle" }
        return "tray.full"
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
