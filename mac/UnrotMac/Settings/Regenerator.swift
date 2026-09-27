//  Regenerator.swift
//  UnrotMac
//
//  Re-running the detector over history from the app: the plan first, then
//  several sessions at once (`runConcurrently`). Stopping starts no more; the
//  ones in flight finish.

import Foundation
import Observation
import UnrotKit

@MainActor
@Observable
final class Regenerator {
    private(set) var plan: RegenPlan?
    private(set) var progress: (done: Int, total: Int)?
    private(set) var totals = (protected: 0, removed: 0, recorded: 0)
    private(set) var lastError: String?
    private var runner: Task<Void, Never>?

    var isRunning: Bool { runner != nil }

    private let client: UnrotClient
    private let store: SurfaceStore
    /// How many sessions to regenerate at once, read when a pass starts.
    private let concurrency: @MainActor () -> Int

    init(client: UnrotClient, store: SurfaceStore,
         concurrency: @escaping @MainActor () -> Int = { 1 }) {
        self.client = client
        self.store = store
        self.concurrency = concurrency
    }

    func refresh() async {
        do {
            plan = try await client.regenPlan()
            lastError = nil
        } catch let error as APIError {
            plan = nil
            lastError = error.message
        } catch {
            plan = nil
        }
    }

    func start() {
        guard runner == nil, let sessions = plan?.toRun, !sessions.isEmpty else { return }
        totals = (0, 0, 0)
        lastError = nil
        runner = Task { [weak self] in
            guard let self else { return }
            self.progress = (0, sessions.count)
            await runConcurrently(
                sessions, atMost: self.concurrency(), proceed: { !Task.isCancelled }
            ) { [weak self] session in
                guard let self else { return false }
                do {
                    let pass = try await self.client.regen(sessionId: session)
                    self.totals.protected += pass.protected
                    self.totals.removed += pass.removed
                    self.totals.recorded += pass.recorded
                } catch let error as APIError {
                    self.lastError = error.message
                    return false
                } catch {
                    self.lastError = "Regeneration stopped."
                    return false
                }
                if let progress = self.progress {
                    self.progress = (progress.done + 1, progress.total)
                }
                return true
            }
            self.progress = nil
            self.runner = nil
            await self.store.load()
            await self.refresh()
        }
    }

    func stop() { runner?.cancel() }
}
