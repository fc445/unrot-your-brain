//  CoreProcess.swift
//  UnrotMac
//
//  Spawns the Python core, watches it, restarts it, and stops it on quit.
//
//  The core is a bundled resource, not an installed program. It is never put
//  on PATH, never written outside the app bundle, and nothing else on the
//  machine is expected to know it exists. `~/.unrot` is shared with the CLI --
//  that is the point, the app is a surface over the same store -- but the
//  process is the app's.
//
//  This lives in UnrotMac rather than UnrotKit because spawning a sidecar is
//  not a thing a phone does. UnrotKit gets a socket path and asks no questions
//  about where it came from.

import Foundation
import Observation
import UnrotKit

@MainActor
@Observable
final class CoreProcess {

    enum Status: Equatable {
        /// Spawned, not yet answering. Normal for the first second or so.
        case starting
        case up(version: String, rawOpen: Bool)
        /// Running somewhere else. We are a second copy and must not spawn.
        case foreign
        /// Not answering, and we are trying again. `attempt` is how many times.
        case down(reason: String, attempt: Int)
        /// Deliberately stopped, or unstartable. No retry pending.
        case stopped(reason: String)

        var isUp: Bool { if case .up = self { return true }; return false }
    }

    private(set) var status: Status = .starting

    /// Extra environment for the core -- the endpoint, model and key from
    /// settings. Added beneath anything already in the app's own environment,
    /// never over it.
    var environmentProvider: (() -> [String: String])?

    let socketPath: String
    private let home: URL

    private var process: Process?
    private var monitor: Task<Void, Never>?
    private var lock: FileHandle?
    private var attempt = 0
    /// True when the running core is the repo's interpreter rather than the
    /// bundled one. Only ever true in a Debug build out of a checkout, and
    /// only used to make one failure legible -- see `hint`.
    private var usingDevelopmentCore = false

    /// Backoff between restart attempts. Capped, because a core that cannot
    /// start will not start on the ninetieth try either, and hammering it just
    /// makes the log unreadable.
    private static let backoff: [Duration] = [
        .milliseconds(250), .milliseconds(500), .seconds(1),
        .seconds(2), .seconds(5), .seconds(10), .seconds(30),
    ]
    private static let pollInterval = Duration.seconds(2)

    init() {
        self.home = Self.unrotHome()
        self.socketPath = home.appending(path: "run/core.sock").path
    }

    #if DEBUG
    /// For the snapshot run only: a status without a process behind it.
    func showForSnapshot(_ status: Status) { self.status = status }
    #endif

    // MARK: - Where things are

    /// `$UNROT_HOME`, else `~/.unrot`. The same order `capture/paths.home()`
    /// uses, because the app and the CLI must agree on which store they mean.
    static func unrotHome() -> URL {
        if let set = ProcessInfo.processInfo.environment["UNROT_HOME"], !set.isEmpty {
            return URL(filePath: (set as NSString).expandingTildeInPath)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appending(path: ".unrot")
    }

    /// The bundled frozen core: `Contents/Resources/unrot-core/unrot-core`.
    private static func frozenCore() -> URL? {
        guard let resources = Bundle.main.resourceURL else { return nil }
        let binary = resources.appending(path: "unrot-core/unrot-core")
        return FileManager.default.isExecutableFile(atPath: binary.path) ? binary : nil
    }

    /// The repo's own interpreter, for the development loop.
    ///
    /// `UnrotDevRepoPath` is set from `$(SRCROOT)/..` at build time, so this
    /// resolves in a Debug build out of a checkout and resolves to nothing
    /// anywhere else. Iterating on the core should not require re-freezing it.
    private static func developmentCore() -> (URL, [String], URL)? {
        guard let repoPath = Bundle.main.object(forInfoDictionaryKey: "UnrotDevRepoPath") as? String,
              !repoPath.isEmpty
        else { return nil }
        let repo = URL(filePath: repoPath).standardizedFileURL
        let python = repo.appending(path: ".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else { return nil }
        return (python, ["-m", "unrot.api"], repo)
    }

    // MARK: - Lifecycle

    func start() {
        guard monitor == nil else { return }
        guard acquireLock() else {
            // A second copy of the app. Refusing to spawn is the whole point:
            // two cores on one socket means one of them is orphaned holding the
            // database open while the other serves a window.
            status = .foreign
            return
        }
        monitor = Task { [weak self] in await self?.supervise() }
    }

    /// Stop the core and stay stopped. Called from `applicationWillTerminate`.
    ///
    /// Synchronous on purpose: the app is going away, and an async teardown
    /// races the process exiting. SIGTERM is what uvicorn handles, and handling
    /// it is what unlinks the socket -- so a clean quit here is what saves the
    /// next launch from having to reclaim a stale one.
    func stop() {
        monitor?.cancel()
        monitor = nil
        terminate(process)
        process = nil
        releaseLock()
        status = .stopped(reason: "Quitting.")
    }

    /// Restart now, without waiting out the backoff. For a menu item later.
    func restart() {
        terminate(process)
        process = nil
        attempt = 0
    }

    // MARK: - The loop

    private func supervise() async {
        let client = UnrotClient(socketPath: socketPath)

        while !Task.isCancelled {
            if process == nil || process?.isRunning != true {
                switch spawn() {
                case .started:
                    status = .starting
                case .impossible(let reason):
                    // Retrying a missing binary forever would only bury the
                    // reason under a restart loop.
                    status = .stopped(reason: reason)
                    return
                }
            }

            do {
                let health = try await client.health(timeout: 5)
                attempt = 0
                status = .up(version: health.version, rawOpen: health.rawOpen)
                try? await Task.sleep(for: Self.pollInterval)
            } catch {
                guard !Task.isCancelled else { return }
                if let exit = exitedStatus() {
                    // The process is gone. Its own exit code says whether that
                    // is worth retrying.
                    if exit == Self.alreadyRunning {
                        status = .foreign
                        return
                    }
                    process = nil
                }
                attempt += 1
                let wait = Self.backoff[min(attempt - 1, Self.backoff.count - 1)]
                let reason = (error as? APIError)?.message ?? "\(error)"
                status = .down(reason: reason + hint(after: attempt), attempt: attempt)
                try? await Task.sleep(for: wait)
            }
        }
    }

    /// The one failure worth explaining rather than just reporting.
    ///
    /// A development core that is *running* and silent, rather than exiting, is
    /// almost always blocked on macOS asking for access to the folder the
    /// checkout is in -- `~/Documents` and `~/Desktop` are both protected, and
    /// a GUI app reading one waits on a consent prompt the user may never have
    /// seen. The process sits in state S producing no output at all, which
    /// looks identical to a hang.
    private func hint(after attempt: Int) -> String {
        guard usingDevelopmentCore, attempt >= 3, process?.isRunning == true else { return "" }
        return "\n\nThe core is running but silent. If this checkout is under"
            + " ~/Documents or ~/Desktop, macOS is probably waiting on a"
            + " folder-access prompt. Grant it, move the checkout, or set"
            + " UnrotUseFrozenCore to use the bundled core instead."
    }

    /// The core's exit code for "a core is already listening there", mirroring
    /// `ALREADY_RUNNING` in `unrot/api/__main__.py`. Distinct from a crash
    /// because it must not be restarted out of.
    private static let alreadyRunning: Int32 = 3

    private func exitedStatus() -> Int32? {
        guard let process, !process.isRunning else { return nil }
        return process.terminationStatus
    }

    private enum Spawn {
        case started
        /// Nothing to retry: there is no core to run.
        case impossible(String)
    }

    private func spawn() -> Spawn {
        let task = Process()
        var environment = [
            "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
            "UNROT_HOME": home.path,
            "PATH": "/usr/bin:/bin",
        ]
        // Anything set in the app's own environment wins, as a real environment
        // variable does for the CLI -- so `unrot.resolver env` and the app
        // describe the same precedence.
        let inherited = ProcessInfo.processInfo.environment
        for (key, value) in inherited
        where key.hasPrefix("UNROT_") || key == "OPENROUTER_API_KEY" || key == "OPENAI_API_KEY" {
            environment[key] = value
        }
        for (key, value) in environmentProvider?() ?? [:] where environment[key] == nil {
            environment[key] = value
        }

        let useFrozen = UserDefaults.standard.bool(forKey: "UnrotUseFrozenCore")
        usingDevelopmentCore = false
        if let frozen = Self.frozenCore(), useFrozen || !Self.isDebugBuild {
            task.executableURL = frozen
            task.arguments = ["--uds", socketPath]
        } else if let (python, arguments, repo) = Self.developmentCore() {
            task.executableURL = python
            task.arguments = arguments + ["--uds", socketPath]
            task.currentDirectoryURL = repo
            environment["PYTHONPATH"] = repo.appending(path: "src").path
            usingDevelopmentCore = true
        } else if let frozen = Self.frozenCore() {
            task.executableURL = frozen
            task.arguments = ["--uds", socketPath]
        } else {
            return .impossible(
                "No core to run. A release build bundles one; a debug build"
                + " expects a .venv in the checkout this was built from."
            )
        }
        task.environment = environment

        // Both streams to a file next to the socket. A supervisor that throws
        // away its child's output can report "not answering" and nothing else,
        // which is the least useful thing it could say -- the core prints the
        // reason on the way down, and without this nobody ever sees it.
        if let log = openLog() {
            task.standardOutput = log
            task.standardError = log
        }

        do {
            try task.run()
        } catch {
            return .impossible("Could not start the core: \(error.localizedDescription)")
        }
        process = task
        return .started
    }

    /// `run/core.log`, appended to across restarts and truncated when it gets
    /// silly. Kept in `run/` because it is runtime state, not something to
    /// keep: it is safe to delete while nothing is running.
    private func openLog() -> FileHandle? {
        let path = home.appending(path: "run/core.log")
        let manager = FileManager.default
        if let size = try? manager.attributesOfItem(atPath: path.path)[.size] as? Int,
           size > 1 << 20 {
            try? manager.removeItem(at: path)
        }
        if !manager.fileExists(atPath: path.path) {
            manager.createFile(atPath: path.path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
        guard let handle = try? FileHandle(forWritingTo: path) else { return nil }
        _ = try? handle.seekToEnd()
        return handle
    }

    private func terminate(_ task: Process?) {
        guard let task, task.isRunning else { return }
        task.terminate()  // SIGTERM: uvicorn handles it and unlinks the socket.
        let deadline = Date().addingTimeInterval(5)
        while task.isRunning && Date() < deadline {
            usleep(50_000)
        }
        if task.isRunning {
            kill(task.processIdentifier, SIGKILL)
        }
    }

    private static var isDebugBuild: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }

    // MARK: - One app, one core

    /// An advisory `flock` on a file beside the socket.
    ///
    /// The core refuses a socket someone is already answering on, so a second
    /// copy could not steal it anyway -- but it would spend its life watching a
    /// process exit with code 3. Failing here instead means the second copy
    /// knows what it is, immediately, and can say so.
    private func acquireLock() -> Bool {
        let path = home.appending(path: "run/app.lock")
        try? FileManager.default.createDirectory(
            at: home.appending(path: "run"),
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let descriptor = open(path.path, O_CREAT | O_RDWR, 0o600)
        guard descriptor >= 0 else { return true }  // Cannot lock; do not block the app.
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            close(descriptor)
            return false
        }
        lock = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        return true
    }

    private func releaseLock() {
        if let lock {
            flock(lock.fileDescriptor, LOCK_UN)
            try? lock.close()
        }
        lock = nil
    }
}
