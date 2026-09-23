//  ExportTests.swift
//  UnrotKitTests
//
//  The wire contract for PR-32's export preview. The JSON here is what
//  `GET /api/export/preview` sends (`ExportPreviewOut` in
//  `src/unrot/api/schemas.py`); a renamed key fails here rather than as an
//  empty "What goes in" list in Settings.

import Foundation
import Testing

@testable import UnrotKit

@Suite("What an export would contain, as the core sends it")
struct ExportTests {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: Data(json.utf8))
    }

    @Test("the preview decodes in full")
    func preview() throws {
        let preview = try decode(ExportPreview.self, """
        {"include_text": false, "filename": "unrot-export-2026-09-23.zip",
         "files": [{"name": "flags.jsonl", "rows": 5}, {"name": "calls.jsonl", "rows": 0}],
         "fixture_events": 15,
         "first_event_at": "2026-09-20T10:00:00.000+00:00", "last_event_at": null,
         "included": ["The event log"], "withheld": ["Explanations you wrote"],
         "never": ["Your API key"]}
        """)
        #expect(preview.filename == "unrot-export-2026-09-23.zip")
        #expect(preview.files.first?.rows == 5)
        #expect(preview.fixtureEvents == 15)
        #expect(preview.lastEventAt == nil)
        #expect(preview.withheld == ["Explanations you wrote"])
    }

    @Test("with text included, nothing is listed as withheld")
    func withText() throws {
        let preview = try decode(ExportPreview.self, """
        {"include_text": true, "filename": "x.zip", "files": [], "fixture_events": 0,
         "first_event_at": null, "last_event_at": null,
         "included": [], "withheld": [], "never": ["Your API key"]}
        """)
        #expect(preview.includeText)
        #expect(preview.withheld.isEmpty)
        #expect(!preview.never.isEmpty)
    }
}
