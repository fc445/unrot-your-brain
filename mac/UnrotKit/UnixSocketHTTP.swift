//  UnixSocketHTTP.swift
//  UnrotKit
//
//  A small HTTP/1.1 client that speaks to a Unix domain socket.
//
//  `URLSession` cannot address a UDS, so this is written rather than
//  configured. Writing an HTTP client is normally a bad idea; it is defensible
//  here only because of everything this one never has to do. Every request is
//  a GET or a JSON POST, every response is small JSON, both ends ship
//  together, and `Connection: close` removes keep-alive and connection reuse.
//  What remains is a request line, some headers, and a body.
//
//  The alternative was swift-nio and async-http-client, which speak
//  `http+unix://` correctly and bring a large dependency tree into an app that
//  talks to a socket on the same machine. Keeping UnrotKit dependency-free is
//  also most of what "compiles for iOS unmodified" costs. If streaming or
//  keep-alive ever becomes necessary, that is the fallback and this goes away.

import Foundation
import Network

public struct HTTPResponse: Sendable {
    public let status: Int
    public let body: Data

    public var isSuccess: Bool { (200..<300).contains(status) }
}

public enum TransportError: Error, Sendable {
    /// Could not reach the socket at all. The core is not running, or not yet.
    case unreachable(String)
    /// Connected, but what came back was not an HTTP response we could read.
    case malformed(String)
    case timedOut

    /// A condition worth another go. Never escapes `UnixSocketHTTP`: the retry
    /// loop either succeeds or converts the last one into `unreachable`, so
    /// callers still only ever see a failure that means something.
    case retryable(String)
}

public struct UnixSocketHTTP: Sendable {
    public let socketPath: String
    public let timeout: TimeInterval

    public init(socketPath: String, timeout: TimeInterval = 120) {
        self.socketPath = socketPath
        self.timeout = timeout
    }

    public func send(
        method: String,
        path: String,
        body: Data? = nil,
        contentType: String = "application/json"
    ) async throws -> HTTPResponse {
        let raw = try await exchange(
            request: encode(method: method, path: path, body: body, contentType: contentType),
            // GET is the only method here that can be repeated safely. Every
            // POST appends an event, and two of them grade an answer twice or
            // generate material twice -- a retry loop that does not know this
            // turns a dropped connection into a duplicate in an append-only log.
            idempotent: method == "GET"
        )
        return try Self.parse(raw)
    }

    // MARK: - Encoding

    private func encode(method: String, path: String, body: Data?, contentType: String) -> Data {
        var head = "\(method) \(path) HTTP/1.1\r\n"
        // A Unix socket has no host, but HTTP/1.1 requires the header. The value
        // is never looked at by anything.
        head += "Host: unrot\r\n"
        head += "Connection: close\r\n"
        head += "Accept: application/json\r\n"
        if let body {
            head += "Content-Type: \(contentType)\r\n"
            head += "Content-Length: \(body.count)\r\n"
        }
        head += "\r\n"

        var data = Data(head.utf8)
        if let body { data.append(body) }
        return data
    }

    // MARK: - The connection

    /// How many times a retryable connection error is worth re-attempting.
    ///
    /// Three, with a few milliseconds between, because the condition being
    /// retried is a momentary one -- see `Exchange.isMomentary`. Anything that
    /// survives three attempts twenty milliseconds apart is not momentary.
    private static let attempts = 3

    private func exchange(request: Data, idempotent: Bool) async throws -> Data {
        var last = "no attempt was made"
        for attempt in 0..<Self.attempts {
            do {
                return try await connectOnce(request: request, idempotent: idempotent)
            } catch TransportError.retryable(let detail) {
                last = detail
                try? await Task.sleep(for: .milliseconds(20 << attempt))
            }
        }
        throw TransportError.unreachable(last)
    }

    private func connectOnce(request: Data, idempotent: Bool) async throws -> Data {
        let exchange = Exchange(socketPath: socketPath, idempotent: idempotent)
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                exchange.start(request: request, timeout: timeout, continuation: continuation)
            }
        } onCancel: {
            exchange.abandon()
        }
    }

    // MARK: - Parsing

    static func parse(_ raw: Data) throws -> HTTPResponse {
        let separator = Data("\r\n\r\n".utf8)
        guard let split = raw.range(of: separator) else {
            throw TransportError.malformed("no header/body separator in \(raw.count) bytes")
        }
        guard let head = String(data: raw[raw.startIndex..<split.lowerBound], encoding: .utf8) else {
            throw TransportError.malformed("headers were not UTF-8")
        }

        var lines = head.components(separatedBy: "\r\n")
        guard !lines.isEmpty else { throw TransportError.malformed("empty response") }
        let statusLine = lines.removeFirst().split(separator: " ", maxSplits: 2).map(String.init)
        guard statusLine.count >= 2, let status = Int(statusLine[1]) else {
            throw TransportError.malformed("unreadable status line: \(head.prefix(80))")
        }

        var headers: [String: String] = [:]
        for line in lines {
            guard let colon = line.firstIndex(of: ":") else { continue }
            let name = line[line.startIndex..<colon].trimmingCharacters(in: .whitespaces).lowercased()
            headers[name] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }

        var body = Data(raw[split.upperBound...])
        // Every /api endpoint today returns a JSONResponse, which carries a
        // Content-Length and is therefore never chunked. This is here anyway:
        // it is twenty lines, and the alternative is that the day someone adds a
        // StreamingResponse, the app starts decoding framing bytes as JSON.
        if headers["transfer-encoding"]?.lowercased().contains("chunked") == true {
            body = try dechunk(body)
        }
        return HTTPResponse(status: status, body: body)
    }

    private static func dechunk(_ body: Data) throws -> Data {
        var out = Data()
        var rest = body[...]
        let crlf = Data("\r\n".utf8)

        while true {
            guard let lineEnd = rest.range(of: crlf) else {
                throw TransportError.malformed("truncated chunk header")
            }
            let header = String(data: rest[rest.startIndex..<lineEnd.lowerBound], encoding: .utf8) ?? ""
            // A chunk size may carry extensions after a semicolon.
            let sizeText = header.split(separator: ";").first.map(String.init) ?? header
            guard let size = Int(sizeText.trimmingCharacters(in: .whitespaces), radix: 16) else {
                throw TransportError.malformed("unreadable chunk size: \(header)")
            }
            if size == 0 { return out }

            let start = lineEnd.upperBound
            let end = rest.index(start, offsetBy: size, limitedBy: rest.endIndex) ?? rest.endIndex
            guard rest.distance(from: start, to: end) == size else {
                throw TransportError.malformed("chunk shorter than its declared size")
            }
            out.append(contentsOf: rest[start..<end])
            let next = rest.index(end, offsetBy: 2, limitedBy: rest.endIndex) ?? rest.endIndex
            rest = rest[next...]
        }
    }
}

// MARK: - One connection, one answer
//
// `NWConnection` is callback-driven and can report more than one terminal
// event for the same connection -- a `failed` state arriving after a completed
// receive, for instance. A continuation resumed twice is a crash, so "exactly
// once" is made a property of this type rather than of the control flow.
//
// It is a class with methods rather than nested closures because those close
// over each other -- the receive loop needs the timer, the timer needs the
// connection -- and under strict concurrency a mutually recursive set of
// `@Sendable` closures cannot be written at all. Methods on one lock-protected
// object say the same thing and compile.

final class Exchange: @unchecked Sendable {
    private let connection: NWConnection
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Data, Error>?
    private var buffer = Data()
    private var deadline: DispatchWorkItem?
    private let idempotent: Bool
    /// Whether the connection ever came up. Before it does, nothing was
    /// written, so the server cannot have seen the request -- which is what
    /// makes retrying safe regardless of method.
    private var reachedReady = false

    init(socketPath: String, idempotent: Bool = true) {
        self.idempotent = idempotent
        connection = NWConnection(to: .unix(path: socketPath), using: .tcp)
    }

    func start(request: Data, timeout: TimeInterval, continuation: CheckedContinuation<Data, Error>) {
        lock.lock()
        self.continuation = continuation
        lock.unlock()

        let timer = DispatchWorkItem { [weak self] in
            self?.settle(.failure(TransportError.timedOut))
        }
        lock.lock()
        deadline = timer
        lock.unlock()
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: timer)

        connection.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            switch state {
            case .ready:
                self.lock.lock()
                self.reachedReady = true
                self.lock.unlock()
                self.send(request)
            case .failed(let error):
                self.settle(self.salvage(or: error))
            case .waiting(let error):
                // Network.framework retries `waiting` indefinitely by design.
                // For a socket on this machine there is nothing to wait for: if
                // it is not answering now it is because the core is not
                // running, and the caller needs telling rather than hanging.
                self.settle(.failure(self.classify(error)))
            default:
                break
            }
        }
        connection.start(queue: .global())
    }

    /// Task cancellation. The continuation is resumed by `withTaskCancellationHandler`'s
    /// own machinery, so this only has to stop the connection.
    func abandon() {
        connection.cancel()
    }

    /// The response, if all of it has already arrived; otherwise the error.
    ///
    /// This is the actual fix for the flakiness, and retrying was only ever
    /// treating a symptom. With `Connection: close`, the server hanging up *is*
    /// how a response ends -- and Network.framework reports that hang-up on a
    /// Unix socket as `.failed(ENETDOWN)`, racing the final receive. When the
    /// state change wins, a response that arrived in full gets thrown away and
    /// reported as the core being down. For a POST that is worse than a false
    /// alarm: the write landed, and the caller is told it did not.
    ///
    /// So before believing an error, check whether the bytes already in hand
    /// are a complete response. Completeness is judged strictly --
    /// `Content-Length` satisfied, or the chunked terminator seen -- because
    /// salvaging a truncated body would be a worse lie than the one it replaces.
    private func salvage(or error: NWError) -> Result<Data, Error> {
        lock.lock()
        let held = buffer
        lock.unlock()
        if Self.isComplete(held) { return .success(held) }
        return .failure(classify(error))
    }

    static func isComplete(_ raw: Data) -> Bool {
        guard let split = raw.range(of: Data("\r\n\r\n".utf8)),
              let head = String(data: raw[raw.startIndex..<split.lowerBound], encoding: .utf8)
        else { return false }
        let bodyCount = raw.distance(from: split.upperBound, to: raw.endIndex)
        for line in head.lowercased().components(separatedBy: "\r\n") {
            if line.hasPrefix("content-length:"),
               let length = Int(line.dropFirst("content-length:".count).trimmingCharacters(in: .whitespaces)) {
                return bodyCount >= length
            }
            if line.hasPrefix("transfer-encoding:"), line.contains("chunked") {
                return raw.suffix(5) == Data("0\r\n\r\n".utf8)
            }
        }
        // No length and not chunked: the only end is the close itself, which is
        // the thing in doubt. Not provably complete, so not salvaged.
        return false
    }

    /// Which connection errors mean "not there", and which mean "try again".
    ///
    /// AF_UNIX has no network, so `ENETDOWN` cannot literally be true -- and
    /// yet Network.framework raises it, reproducibly, under load and on rapid
    /// reconnects. Reported as fatal it shows up as the core being down while
    /// the core is plainly answering, which is the worst possible lie this
    /// client can tell: `SurfaceState.failed` is derived from exactly this.
    ///
    /// The two codes that really do mean the socket is not there are `ENOENT`
    /// (no file at the path) and `ECONNREFUSED` (a file, but nobody
    /// listening). Those are never retried.
    static func isMomentary(_ error: NWError) -> Bool {
        if case .posix(let code) = error, code == .ENOENT || code == .ECONNREFUSED {
            return false
        }
        return true
    }

    /// Whether this particular failure may be re-attempted.
    ///
    /// Two conditions, and both have to hold. The error has to be a momentary
    /// one, and the request has to be repeatable -- which means either the
    /// connection never came up (so nothing was sent and the server cannot
    /// have acted) or the method is idempotent. A POST that failed after the
    /// connection was live may well have been received and acted on, and
    /// retrying it would append the event a second time.
    private func classify(_ error: NWError) -> TransportError {
        guard Self.isMomentary(error) else { return .unreachable("\(error)") }
        lock.lock()
        let started = reachedReady
        lock.unlock()
        return (!started || idempotent) ? .retryable("\(error)") : .unreachable("\(error)")
    }

    private func send(_ request: Data) {
        connection.send(content: request, completion: .contentProcessed { [weak self] error in
            guard let self else { return }
            if let error {
                return self.settle(.failure(self.classify(error)))
            }
            self.receive()
        })
    }

    private func receive() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 1 << 16) {
            [weak self] chunk, _, isComplete, error in
            guard let self else { return }
            if let chunk, !chunk.isEmpty {
                self.lock.lock()
                self.buffer.append(chunk)
                self.lock.unlock()
            }
            if let error {
                return self.settle(self.salvage(or: error))
            }
            if isComplete {
                // The server hung up, which with `Connection: close` is how a
                // complete response ends.
                self.lock.lock()
                let body = self.buffer
                self.lock.unlock()
                return self.settle(.success(body))
            }
            self.receive()
        }
    }

    private func settle(_ result: Result<Data, Error>) {
        lock.lock()
        let pending = continuation
        continuation = nil
        let timer = deadline
        deadline = nil
        lock.unlock()

        guard pending != nil else { return }
        timer?.cancel()
        connection.stateUpdateHandler = nil
        connection.cancel()
        pending?.resume(with: result)
    }
}
