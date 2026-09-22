//  TransportTests.swift
//  UnrotKitTests
//
//  The transport, driven against a stub Unix socket server.
//
//  A stub rather than the real core, because these are about the parts of the
//  exchange the core is not involved in: framing, status lines, chunked
//  bodies, and a server that hangs up mid-response. Those are the failure
//  modes a hand-written HTTP client has and a library one would not, so they
//  are the ones worth paying for in tests.

import Foundation
import Testing

@testable import UnrotKit

/// A one-shot Unix socket server that replies with whatever bytes it is given.
///
/// Deliberately POSIX rather than Network.framework: a stub that shares an
/// implementation with the thing under test can agree with it about something
/// they are both wrong on.
enum StubError: Error { case cannotListen(String) }

final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    func bump() { lock.lock(); count += 1; lock.unlock() }
    var value: Int { lock.lock(); defer { lock.unlock() }; return count }
}

final class StubServer: @unchecked Sendable {
    let path: String
    private let listener: Int32
    private var thread: Thread?
    private let counter = Counter()

    /// How many connections this stub has served.
    var accepted: Int { counter.value }

    /// Replies with the same bytes to every request.
    convenience init(reply: Data) throws {
        try self.init { _, _ in reply }
    }

    /// Replies per request. `route` gets the method and path and returns the
    /// whole raw response, so a test can play a small server with state.
    init(route: @escaping @Sendable (_ method: String, _ path: String) -> Data) throws {
        let directory = FileManager.default.temporaryDirectory
            .appending(path: "unrot-test-\(UInt32.random(in: 0..<UInt32.max))")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        self.path = directory.appending(path: "s").path

        listener = socket(AF_UNIX, SOCK_STREAM, 0)
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        _ = withUnsafeMutablePointer(to: &address.sun_path) { raw in
            path.withCString { source in
                strncpy(UnsafeMutableRawPointer(raw).assumingMemoryBound(to: CChar.self), source, 103)
            }
        }
        let size = socklen_t(MemoryLayout<sockaddr_un>.size)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(listener, $0, size) }
        }
        guard bound == 0, listen(listener, 4) == 0 else {
            throw StubError.cannotListen(String(cString: strerror(errno)))
        }

        let descriptor = listener
        // Serves until the listener is closed, rather than once. A one-shot
        // stub cannot model a retry: the first attempt consumes the only
        // accept, and the second hangs against a listening socket nobody is
        // reading -- which presents as a timeout in the client rather than as
        // the missing feature it is.
        let counter = self.counter
        thread = Thread {
            while true {
                let client = accept(descriptor, nil, nil)
                guard client >= 0 else { return }
                counter.bump()
                var scratch = [UInt8](repeating: 0, count: 4096)
                let count = read(client, &scratch, scratch.count)
                let head = String(decoding: scratch.prefix(max(0, count)), as: UTF8.self)
                let parts = head.split(separator: " ", maxSplits: 2).map(String.init)
                let reply = route(parts.first ?? "", parts.count > 1 ? parts[1] : "")
                reply.withUnsafeBytes { _ = write(client, $0.baseAddress, reply.count) }
                close(client)
            }
        }
        thread?.start()
    }

    deinit { close(listener) }
}

@Suite("The socket transport")
struct TransportTests {

    @Test("a complete response is read to EOF and parsed")
    func completeResponse() async throws {
        let payload = #"{"ok":true,"version":"0.1.0","compiled_schema":3,"raw_open":false,"store_path":"/x"}"#
        let reply = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: \(payload.utf8.count)\r\nConnection: close\r\n\r\n\(payload)"
        let server = try StubServer(reply: Data(reply.utf8))

        let health = try await UnrotClient(socketPath: server.path).health(timeout: 5)

        #expect(health.ok)
        #expect(health.compiledSchema == 3)
        #expect(health.rawOpen == false)
    }

    @Test("a chunked body is decoded rather than handed over as framing bytes")
    func chunkedResponse() throws {
        // No /api endpoint chunks today. This exists so that the day one does,
        // the client does not start decoding hex lengths as JSON.
        let reply = "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            + "4\r\n{\"a\"\r\n5\r\n:1}  \r\n0\r\n\r\n"
        let parsed = try UnixSocketHTTP.parse(Data(reply.utf8))

        #expect(parsed.status == 200)
        #expect(String(data: parsed.body, encoding: .utf8) == #"{"a":1}  "#)
    }

    @Test("a socket nothing is listening on is unreachable, not empty")
    func nothingListening() async {
        let client = UnrotClient(socketPath: "/tmp/unrot-does-not-exist-\(UUID().uuidString).sock")
        await #expect(throws: APIError.self) { try await client.surface() }
    }

    @Test("an error status carries the server's own words")
    func refusalKeepsTheServersWording() async throws {
        let payload = #"{"detail":"no concept 'nope'"}"#
        let reply = "HTTP/1.1 404 Not Found\r\nContent-Length: \(payload.utf8.count)\r\n\r\n\(payload)"
        let server = try StubServer(reply: Data(reply.utf8))

        do {
            _ = try await UnrotClient(socketPath: server.path).check(conceptId: "nope")
            Issue.record("a 404 should not decode as a Check")
        } catch let error as APIError {
            #expect(error.message == "no concept 'nope'")
            // A refusal is the core answering. It must not be mistaken for the
            // core being down, which is what would blank the whole page.
            #expect(error.isTransport == false)
        }
    }

    @Test("ENETDOWN on a Unix socket is retried, not reported as a dead core")
    func networkIsDownIsNotATruth() {
        // AF_UNIX has no network. Network.framework raises ENETDOWN anyway,
        // reproducibly, under load -- it was making this suite flaky roughly
        // one run in thirty. Reported as fatal it renders as the core being
        // down while the core is answering, and `SurfaceState.failed` is
        // derived from precisely that.
        #expect(Exchange.isMomentary(.posix(.ENETDOWN)))
        #expect(Exchange.isMomentary(.posix(.EAGAIN)))

        // The two that genuinely mean the socket is not there.
        #expect(!Exchange.isMomentary(.posix(.ENOENT)))
        #expect(!Exchange.isMomentary(.posix(.ECONNREFUSED)))
    }

    @Test("a POST is never retried once the connection was live")
    func aWriteIsNeverRepeated() async throws {
        // Every POST appends an event. A connection that dropped after the
        // request went out may well have been received and acted on, so a
        // retry would grade an answer twice, or generate material twice, or
        // put a second judgment in an append-only log. The server closing
        // without answering is the shape that failure takes.
        let server = try StubServer(reply: Data())

        let client = UnrotClient(socketPath: server.path, timeout: 3)
        do {
            _ = try await client.judge(encounterId: "e1", .confirm)
            Issue.record("an empty reply should not decode as a judgment")
        } catch let error as APIError {
            #expect(error.isTransport)
        }
        #expect(server.accepted == 1, "the write was re-sent \(server.accepted) times")
    }

    @Test("a response that fully arrived survives the connection dropping after it")
    func completeResponsesAreSalvaged() {
        // The server hanging up is how a `Connection: close` response ends, and
        // Network.framework reports that as a failure racing the last receive.
        let body = #"{"ok":true}"#
        let whole = Data("HTTP/1.1 200 OK\r\nContent-Length: \(body.utf8.count)\r\n\r\n\(body)".utf8)
        #expect(Exchange.isComplete(whole))

        // ...but only when provably whole. A short body is not salvaged.
        #expect(!Exchange.isComplete(whole.dropLast(3)))
        // Nor is one with no length, whose only end is the close in question.
        #expect(!Exchange.isComplete(Data("HTTP/1.1 200 OK\r\n\r\n{}".utf8)))
        // A chunked body is complete at its terminator and not before.
        let chunked = Data("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n".utf8)
        #expect(Exchange.isComplete(chunked))
        #expect(!Exchange.isComplete(chunked.dropLast(2)))
    }

    @Test("a truncated response is a transport failure, never a partial answer")
    func truncatedResponse() throws {
        #expect(throws: TransportError.self) {
            try UnixSocketHTTP.parse(Data("HTTP/1.1 200 OK\r\nContent-Len".utf8))
        }
    }
}
