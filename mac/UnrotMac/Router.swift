//  Router.swift
//  UnrotMac
//
//  Which sheet the window is showing. Shared rather than held in a view, so the
//  popover's "Show the moment in the window" can ask the window for it.

import Foundation
import Observation

@MainActor
@Observable
final class Router {
    struct Moment: Identifiable, Hashable {
        let conceptId: String
        let encounterId: String
        var id: String { encounterId }
    }

    struct CheckRequest: Identifiable, Hashable {
        let conceptId: String
        var id: String { conceptId }
    }

    var moment: Moment?
    var check: CheckRequest?
}
