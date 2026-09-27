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
    /// For asking, on Waiting on you, whether to re-examine history the
    /// detector has since changed under.
    var regenerator: Regenerator? = nil

    /// Whether the page, rather than the sidebar, has the keyboard. K and D
    /// only mean something to the page, so it starts with it.
    @FocusState private var pageHasKeyboard: Bool
    @Environment(\.undoManager) private var undoManager

    /// The cards the arrows move through: the list on screen's.
    private var cards: [Concept] { list.bucket == .closed ? [] : store.concepts(in: list.bucket) }
    /// The watcher does not start until the first run is finished, so the bar must not
    /// claim it is watching while the user is still being asked whether it may.
    private var settingUp: Bool { onboarding.map { !$0.done } ?? false }
    /// The card K and D answer and the Gap menu acts on. The arrows move it;
    /// it defaults to the top.
    private var focusedConcept: Concept? { router.focusedConcept(in: store) }
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
            await regenerator?.refresh()
            var ticks = 0
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard core.status.isUp else { continue }
                await store.load()
                // The plan changes when the detector or the history does,
                // which is rarely: once a minute is plenty.
                ticks += 1
                if ticks % 12 == 0 { await regenerator?.refresh() }
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
            // The Gap menu's words, so Edit › Undo names what was chosen there.
            undoManager?.setActionName(answer.verdict == .confirm ? "“I Didn't Know This”" : "“I Knew It”")
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
        guard !cards.isEmpty else { return }
        let index = cards.firstIndex { $0.conceptId == focusedConcept?.conceptId } ?? 0
        router.focusedGap = cards[max(0, min(cards.count - 1, index + step))].conceptId
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
                        .font(.system(.body))
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
        // letter fires even while a text editor has focus. Only a waiting card
        // has a question to answer, so everywhere else K and D fall through.
        .focusable()
        .focusEffectDisabled()
        .focused($pageHasKeyboard)
        .onAppear { pageHasKeyboard = true }
        .onKeyPress(keys: [.upArrow, .downArrow]) { press in
            guard !cards.isEmpty else { return .ignored }
            move(press.key == .upArrow ? -1 : 1)
            return .handled
        }
        .onKeyPress(characters: CharacterSet(charactersIn: "dDkK"), phases: .down) { press in
            guard let concept = focusedConcept, concept.bucket == .open,
                  let encounter = concept.unanswered else { return .ignored }
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
            .font(.system(.body))
            .foregroundStyle(Color.inkSoft)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 4)
        if list.bucket == .open {
            QueueBar(watcher: watcher).padding(.top, 14)
            if let regenerator {
                ReexamineBanner(regenerator: regenerator, watcher: watcher).padding(.top, 10)
            }
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
                        isFocused: concept.conceptId == focusedConcept?.conceptId
                    )
                    .onTapGesture {
                        router.focusedGap = concept.conceptId
                        pageHasKeyboard = true
                    }
                    .contextMenu {
                        GapContextMenu(actions: GapActions(concept: concept, quick: quick, router: router))
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
                    .font(.system(.callout, weight: .medium))
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
            .font(.system(.callout))
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

/// The detector changed since some of the history was analysed. Asked here,
/// beside the gaps a re-examination would change, rather than in Settings,
/// which is for choices made once. The plan itself stays in Settings ›
/// Advanced, and Show the Plan opens it there.
private struct ReexamineBanner: View {
    let regenerator: Regenerator
    let watcher: Watcher
    @Environment(\.openSettings) private var openSettings
    @State private var confirming = false

    var body: some View {
        if let progress = regenerator.progress {
            bar(
                title: "Re-examining \(min(progress.done + 1, progress.total)) of \(progress.total)…",
                text: "Your judgments are not touched. It can stop between any two sessions."
            ) {
                Button("Stop") { regenerator.stop() }.buttonStyle(UnrotButton())
            }
        } else if let previously, previously > 0, let plan = regenerator.plan, plan.canRun {
            bar(
                title: "The detector changed. Re-examine your history?",
                text: "\(previously) session\(previously == 1 ? " was" : "s were") analysed by an older detector. \(plan.protected) of your judgments are protected and replayed untouched."
            ) {
                Button("Show the plan") {
                    UserDefaults.standard.set(SettingsView.Tab.advanced.rawValue, forKey: SettingsView.tabKey)
                    openSettings()
                }
                .buttonStyle(UnrotButton())
                Button("Re-examine…") { confirming = true }.buttonStyle(UnrotButton(weight: .primary))
            }
            .confirmationDialog("Re-examine \(plan.toRun.count) sessions?", isPresented: $confirming) {
                Button("Re-examine") { regenerator.start() }
            } message: {
                Text("This makes model calls for each session, and costs what that costs. It stops between any two sessions, and your judgments are not touched.")
            }
        }
    }

    /// Sessions a regeneration would re-run that were already analysed once --
    /// i.e. by a detector that has since changed. The never-analysed ones are
    /// the queue's business, not this banner's.
    private var previously: Int? {
        guard let plan = regenerator.plan else { return nil }
        let never = watcher.queue?.pending.filter { $0.reason == "never" }.count ?? 0
        return max(0, plan.toRun.count - never)
    }

    private func bar<Buttons: View>(title: String, text: String, @ViewBuilder buttons: () -> Buttons) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "arrow.triangle.2.circlepath").foregroundStyle(Color.bucketLearning)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(.callout, weight: .semibold)).foregroundStyle(Color.bucketLearning)
                Text(text).font(.system(.callout)).foregroundStyle(Color.inkSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            buttons()
        }
        .padding(10)
        .background(Color.bucketLearningBG, in: RoundedRectangle(cornerRadius: 8))
    }
}

/// Seeded data must never read as a finding about the user.
private struct FixturesNotice: View {
    let count: Int

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Pip(text: "fixtures", tint: .inkFaint, wash: .rule)
            Text("\(count) of these events are development fixtures. Remove them with `python -m unrot.store seed --clear`.")
                .font(.system(.callout))
                .foregroundStyle(Color.inkSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
    }
}
