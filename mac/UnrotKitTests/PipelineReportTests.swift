//  PipelineReportTests.swift
//  UnrotKitTests
//
//  The wire contract for spot checks and the pipeline report. Both bodies below
//  were produced by the core itself (`PipelineOut` and `EncounterOut` dumped
//  from a real computed report), so a field renamed on either side fails here
//  rather than as a blank Developer tab.

import Foundation
import Testing

@testable import UnrotKit

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    // The same configuration `UnrotClient` decodes with.
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(T.self, from: Data(json.utf8))
}

@Test func anEncounterSaysWhenItIsASpotCheck() throws {
    let encounter = try decode(Encounter.self, #"""
    {"encounter_id":"e-1","source":"transcript","paraphrase":null,"judgment":null,"judged_at":null,"occurred_at":"2026-09-26T10:00:00Z","detector_version":null,"session_id":null,"line_start":null,"line_end":null,"resolvable":false,"repo":null,"spot_check":true}
    """#)
    #expect(encounter.spotCheck == true)
}

@Test func anOlderCoreWithoutSpotChecksStillDecodes() throws {
    let encounter = try decode(Encounter.self, #"""
    {"encounter_id":"e-1","source":"transcript","occurred_at":"2026-09-26T10:00:00Z","resolvable":false}
    """#)
    #expect(encounter.spotCheck == nil)
}

@Test func thePipelineReportDecodesAsTheCoreSendsIt() throws {
    let report = try decode(PipelineReport.self, #"""
    {"since":"2026-09-19T12:18:40+00:00","until":"2026-09-26T12:18:40+00:00","fixtures_excluded":0,"sessions":0,"detector_calls":0,"found":0,"gaps":0,"judged":0,"passed":0,"held_back":0,"spot_checks":0,"not_judged":0,"filed":0,"confirmed":0,"dismissed":0,"unanswered":0,"flag_precision":{"part":0,"whole":0,"rate":null},"hold_back_precision":{"part":0,"whole":0,"rate":null},"calibration":{"under 0.1":{"part":0,"whole":0,"rate":null},"0.1 to 0.3":{"part":0,"whole":0,"rate":null},"0.3 and over":{"part":0,"whole":0,"rate":null}},"stages":{"detection":{"calls":1,"failed":0,"cost":0.001,"unpriced":0,"median_ms":30000.0,"cost_text":"$0.0010"},"familiarity":{"calls":1,"failed":0,"cost":0.00002,"unpriced":0,"median_ms":250.0,"cost_text":"<$0.0001"},"resolution":{"calls":0,"failed":0,"cost":0.0,"unpriced":0,"median_ms":null,"cost_text":"$0.00"}}}
    """#)

    #expect(report.holdBackPrecision.rate == nil)
    // Every band and stage the view looks up by name is present under that name.
    #expect(Set(report.calibration.keys) == Set(PipelineReport.bands))
    #expect(PipelineReport.stageNames.allSatisfy { report.stages[$0.0] != nil })
    #expect(report.stages["detection"]?.medianMs == 30000)
    #expect(report.stages["resolution"]?.medianMs == nil)
    #expect(report.stages["familiarity"]?.costText == "<$0.0001")
}
