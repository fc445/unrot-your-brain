//  SpendTests.swift
//  UnrotKitTests
//
//  The wire contract for PR-31's spend figures. The JSON here is what
//  `src/unrot/api/schemas.py` sends; if a key is renamed there, this fails
//  here rather than as a silently empty row in Settings.

import Foundation
import Testing

@testable import UnrotKit

@Suite("What analysis has cost, as the core sends it")
struct SpendTests {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: Data(json.utf8))
    }

    private let total = """
    {"calls": 3, "failed": 1, "cost": 0.009, "priced": 3, "local": 0, "unpriced": 0,
     "prompt_tokens": 3600, "completion_tokens": 33128, "reasoning_tokens": 30000,
     "local_only": false, "text": "$0.0090"}
    """

    @Test("the queue carries the week and an estimate")
    func queueWithSpend() throws {
        let queue = try decode(AnalysisQueue.self, """
        {"pending": [], "can_analyse": true, "analysing": [],
         "estimate": {"model": "m", "sessions": 19, "based_on": 20, "per_session": 0.006,
                      "local": false, "cost": 0.114, "text": "about $0.11"},
         "spent_this_week": \(total)}
        """)
        #expect(queue.spentThisWeek?.text == "$0.0090")
        #expect(queue.spentThisWeek?.failed == 1)
        #expect(queue.estimate?.text == "about $0.11")
        #expect(queue.estimate?.basedOn == 20)
    }

    @Test("an older core, which sends neither, still decodes")
    func queueWithoutSpend() throws {
        let queue = try decode(AnalysisQueue.self, #"{"pending": [], "can_analyse": false, "analysing": []}"#)
        #expect(queue.spentThisWeek == nil)
        #expect(queue.estimate == nil)
    }

    @Test("the Settings breakdown decodes in full")
    func breakdown() throws {
        let spend = try decode(Spend.self, """
        {"model": "m", "local": false, "week": \(total),
         "week_by_purpose": [{"purpose": "detection", "label": "finding gaps", "total": \(total)}],
         "all_time": \(total), "all_time_by_purpose": [],
         "by_day": [{"day": "2026-09-23", "total": \(total)}],
         "per_session": [{"model": "m", "sessions": 2, "cost": 0.009, "local": false,
                          "average": 0.0045, "text": "$0.0045"}],
         "window": null}
        """)
        #expect(spend.weekByPurpose.first?.label == "finding gaps")
        #expect(spend.perSession.first?.text == "$0.0045")
        #expect(spend.byDay.first?.day == "2026-09-23")
        #expect(spend.window == nil)
    }
}
