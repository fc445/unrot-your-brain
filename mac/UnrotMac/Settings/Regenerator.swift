//  Regenerator.swift
//  UnrotMac
//
//  Re-running the detector over history from the app: the plan first, then one
//  session at a time, stoppable between any two.

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

    init(client: UnrotClient, store: SurfaceStore) {
        self.client = client
        self.store = store
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
            for (index, session) in sessions.enumerated() {
                if Task.isCancelled { break }
                do {
                    let pass = try await self.client.regen(sessionId: session)
                    self.totals.protected += pass.protected
                    self.totals.removed += pass.removed
                    self.totals.recorded += pass.recorded
                } catch let error as APIError {
                    self.lastError = error.message
                    break
                } catch {
                    self.lastError = "Regeneration stopped."
                    break
                }
                self.progress = (index + 1, sessions.count)
            }
            self.progress = nil
            self.runner = nil
            await self.store.load()
            await self.refresh()
        }
    }

    func stop() { runner?.cancel() }
}
