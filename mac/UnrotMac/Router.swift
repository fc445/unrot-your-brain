//  Router.swift
//  UnrotMac
//
//  Which list the window is on, and which sheet it is showing. Shared rather
//  than held in a view, so the menu bar, a notification and the popover's
//  "Show the moment in the window" can ask the window for either.

import Foundation
import Observation
import UnrotKit

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

    /// The sidebar's selection. Optional because a sidebar can be ⌘-clicked
    /// empty; the window reads nil as Waiting on you.
    var list: Bucket? = .open
    var moment: Moment?
    var check: CheckRequest?
}
