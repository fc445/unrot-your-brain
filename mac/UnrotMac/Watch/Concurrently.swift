//  Concurrently.swift
//  UnrotMac
//
//  Several sessions analysed at once, for the Watcher and the Regenerator.
//
//  A session's analysis is one request to the core, and nearly all of it is the
//  core waiting on the model provider -- a minute or more per detection call.
//  Sent one after another, a backlog of twenty takes twenty of those minutes;
//  four at a time, about five, for the same calls and the same cost. The core
//  keeps its own filing one session at a time (`analyse.FILING`), so running
//  them side by side here changes how long it takes, not what it finds.

/// Runs `work` for each item, at most `limit` at a time, starting them in order.
///
/// Everything runs on the main actor. The work is waiting on a socket, not
/// computing, so that costs nothing, and it means the callers' state -- the
/// progress, the error, the set of sessions in flight -- needs no locking.
///
/// Before each item starts, `proceed` is asked; once it says no (a stop, a
/// pause) nothing new starts. `work` returns false to say the same, after a
/// failure. Cancelling the calling task cancels the work in flight, as it did
/// when there was only ever one.
@MainActor
func runConcurrently<Item: Sendable>(
    _ items: [Item],
    atMost limit: Int,
    proceed: @escaping @MainActor () -> Bool,
    _ work: @escaping @MainActor (Item) async -> Bool
) async {
    let queue = WorkQueue(items)
    // A fixed set of workers pulling from one queue, rather than a task group:
    // the same shape, and one the strict-concurrency checker can follow.
    let workers = (0..<min(max(1, limit), items.count)).map { _ in
        Task { @MainActor in
            while let item = queue.take(if: proceed) {
                if await !work(item) { queue.stop() }
            }
        }
    }
    await withTaskCancellationHandler {
        for worker in workers { await worker.value }
    } onCancel: {
        for worker in workers { worker.cancel() }
    }
}

@MainActor
private final class WorkQueue<Item> {
    private let items: [Item]
    private var next = 0
    private var stopped = false

    init(_ items: [Item]) { self.items = items }

    func take(if proceed: () -> Bool) -> Item? {
        guard !stopped, next < items.count, proceed() else { return nil }
        defer { next += 1 }
        return items[next]
    }

    func stop() { stopped = true }
}
