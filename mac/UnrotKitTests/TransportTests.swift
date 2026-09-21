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

final class StubServer: @unchecked Sendable {
    let path: String
    private let listener: Int32
    private var thread: Thread?

    init(reply: Data, holdOpen: Bool = false) throws {
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
        thread = Thread {
            let client = accept(descriptor, nil, nil)
            guard client >= 0 else { return }
            var scratch = [UInt8](repeating: 0, count: 4096)
            _ = read(client, &scratch, scratch.count)
            reply.withUnsafeBytes { _ = write(client, $0.baseAddress, reply.count) }
            if !holdOpen { close(client) }
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

    @Test("a truncated response is a transport failure, never a partial answer")
    func truncatedResponse() throws {
        #expect(throws: TransportError.self) {
            try UnixSocketHTTP.parse(Data("HTTP/1.1 200 OK\r\nContent-Len".utf8))
        }
    }
}
