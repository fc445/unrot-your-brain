//  Watcher.swift
//  UnrotMac
//
//  The reason the app exists. The PRD: "Manual trigger is fine for v1; a
//  watcher/daemon can come later." This is later.
//
//  Two jobs, split where the spending is:
//
//  * **Capture** -- once a session has gone quiet, copy its transcript into the
//    raw store. Claude Code already wrote it to disk; unrot keeping a copy adds
//    no exposure and costs nothing. On unless paused.
//  * **Analysis** -- detect and resolve. Model calls, so money. Runs when the
//    user clicks "Analyse now", or automatically only if they switched that on
//    -- and even then only for sessions that finished *after* they did. A
//    backlog is never billed as a side effect of flipping a switch.
//
//  The queue itself is the core's (`GET /api/queue`), derived from the stores,
//  so it survives relaunch without this keeping anything.

import CoreServices
import Foundation
import Observation
import UnrotKit

@MainActor
@Observable
final class Watcher {

    // MARK: Settings, persisted

    var paused: Bool {
        didSet {
            defaults.set(paused, forKey: Keys.paused)
            if paused { runner?.cancel() } else { Task { await sweep() } }
        }
    }

    /// Off by default. PR-29's open question -- "does the watcher spend money
    /// without asking?" -- is answered no until the user answers yes.
    var autoAnalyse: Bool {
        didSet {
            defaults.set(autoAnalyse, forKey: Keys.auto)
            // Only sessions captured from here on. Switching this on must not
            // quietly analyse everything already waiting.
            eligible = []
        }
    }

    var quietMinutes: Int {
        didSet { defaults.set(quietMinutes, forKey: Keys.quiet) }
    }

    // MARK: State

    private(set) var queue: AnalysisQueue?
    /// The session being analysed right now, if any.
    private(set) var analysing: String?
    private(set) var progress: (done: Int, total: Int)?
    private(set) var lastError: String?

    var pendingCount: Int { queue?.pending.count ?? 0 }
    var canAnalyse: Bool { queue?.canAnalyse ?? false }
    var isRunning: Bool { runner != nil }
    /// False until the first run is finished and `start()` is called.
    private(set) var started = false

    // MARK: -

    private enum Keys {
        static let paused = "UnrotWatchingPaused"
        static let auto = "UnrotAnalyseAutomatically"
        static let quiet = "UnrotQuietMinutes"
    }

    private let defaults = UserDefaults.standard
    private let client: UnrotClient
    private let store: SurfaceStore
    private let core: CoreProcess

    /// Transcript path -> when it last changed.
    private var changes: [String: Date] = [:]
    /// Sessions the watcher captured since automatic analysis was switched on.
    private var eligible: Set<String> = []
    private var stream: FSEventStreamRef?
    private var sweeper: Task<Void, Never>?
    private var runner: Task<Void, Never>?

    init(client: UnrotClient, store: SurfaceStore, core: CoreProcess) {
        self.client = client
        self.store = store
        self.core = core
        paused = defaults.bool(forKey: Keys.paused)
        autoAnalyse = defaults.bool(forKey: Keys.auto)
        quietMinutes = defaults.object(forKey: Keys.quiet) as? Int ?? Int(WatchPolicy.defaultQuiet / 60)
    }

    func start() {
        guard !started else { return }
        started = true
        seed()
        listen()
        sweeper = Task { [weak self] in
            while !Task.isCancelled {
                await self?.sweep()
                try? await Task.sleep(for: .seconds(30))
            }
        }
    }

    // MARK: - Noticing

    /// Sessions that finished while the app was not running: anything written
    /// in the last week goes through the same quiet-period rule on the first
    /// sweep. Capture is idempotent, so re-offering one already captured costs
    /// a comparison and nothing else.
    private func seed() {
        let cutoff = Date().addingTimeInterval(-7 * 24 * 3600)
        guard let files = FileManager.default.enumerator(
            at: ClaudeActivity.projects,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles, .skipsPackageDescendants]
        ) else { return }
        for case let url as URL in files where url.pathExtension == "jsonl" {
            if let modified = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate, modified > cutoff {
                changes[url.path] = modified
            }
        }
    }

    /// FSEvents on Claude Code's projects directory, file-level, coalesced over
    /// two seconds. Read-only: this learns *that* a transcript changed, and the
    /// core reads it later -- nothing here opens a file in ~/.claude.
    private func listen() {
        let root = ClaudeActivity.projects.path
        guard FileManager.default.fileExists(atPath: root) else { return }

        var context = FSEventStreamContext(
            version: 0,
            info: Unmanaged.passUnretained(self).toOpaque(),
            retain: nil, release: nil, copyDescription: nil
        )
        let callback: FSEventStreamCallback = { _, info, _, paths, _, _ in
            guard let info else { return }
            let watcher = Unmanaged<Watcher>.fromOpaque(info).takeUnretainedValue()
            let changed = (unsafeBitCast(paths, to: NSArray.self) as? [String]) ?? []
            // Delivered on the main queue, set below.
            MainActor.assumeIsolated { watcher.noticed(changed) }
        }
        let flags = FSEventStreamCreateFlags(
            kFSEventStreamCreateFlagFileEvents | kFSEventStreamCreateFlagUseCFTypes | kFSEventStreamCreateFlagNoDefer
        )
        guard let stream = FSEventStreamCreate(
            nil, callback, &context, [root] as CFArray,
            FSEventStreamEventId(kFSEventStreamEventIdSinceNow), 2.0, flags
        ) else { return }
        FSEventStreamSetDispatchQueue(stream, .main)
        FSEventStreamStart(stream)
        self.stream = stream
    }

    private func noticed(_ paths: [String]) {
        let now = Date()
        for path in paths where path.hasSuffix(".jsonl") {
            changes[path] = now
        }
    }

    // MARK: - The sweep

    func sweep() async {
        guard !paused, core.status.isUp else { return }

        let snapshot = changes
        let ready = WatchPolicy.ready(changes: snapshot, now: Date(), quiet: TimeInterval(quietMinutes * 60))
        if !ready.isEmpty {
            do {
                let result = try await client.capture(paths: ready)
                for path in ready where changes[path] == snapshot[path] {
                    // Only forget a path if it did not change again while the
                    // capture was in flight; if it did, it waits its turn anew.
                    changes[path] = nil
                }
                if autoAnalyse {
                    eligible.formUnion(
                        result.captured
                            .filter { $0.status != "unchanged" && $0.status != "empty" && !$0.status.hasPrefix("error") }
                            .map(\.sessionId)
                    )
                }
                lastError = nil
            } catch let error as APIError {
                lastError = "Capture failed: \(error.message)"
            } catch {
                lastError = "Capture failed."
            }
        }

        await refreshQueue()

        if autoAnalyse, runner == nil, canAnalyse, let queue {
            let due = queue.pending.filter { eligible.contains($0.sessionId) }
            if !due.isEmpty { run(due) }
        }
    }

    func refreshQueue() async {
        guard core.status.isUp else { return }
        queue = try? await client.queue()
    }

    // MARK: - Analysis

    /// Everything waiting, now. The explicit version of spending.
    func analyseNow() {
        guard runner == nil else { return }
        guard canAnalyse else {
            lastError = "No model is configured, so nothing can be analysed. Captured sessions will wait."
            return
        }
        guard let pending = queue?.pending, !pending.isEmpty else { return }
        run(pending)
    }

    func stopAnalysing() {
        runner?.cancel()
    }

    /// One session at a time, so progress is real and pausing takes effect
    /// between sessions. Stops at the first failure rather than paying for the
    /// same error on every remaining session.
    private func run(_ sessions: [PendingSession]) {
        runner = Task { [weak self] in
            guard let self else { return }
            self.progress = (0, sessions.count)
            self.lastError = nil
            for (index, session) in sessions.enumerated() {
                if Task.isCancelled || self.paused { break }
                self.analysing = session.sessionId
                do {
                    _ = try await self.client.analyse(sessionId: session.sessionId)
                    self.eligible.remove(session.sessionId)
                } catch let error as APIError {
                    if case .refused(409, let detail) = error, detail.contains("already being analysed") {
                        // Someone else is on it. Not a failure.
                    } else {
                        self.lastError = error.message
                        break
                    }
                } catch {
                    self.lastError = "Analysis failed."
                    break
                }
                self.progress = (index + 1, sessions.count)
                await self.store.load()
            }
            self.analysing = nil
            self.progress = nil
            self.runner = nil
            await self.store.load()
            await self.refreshQueue()
        }
    }
}
