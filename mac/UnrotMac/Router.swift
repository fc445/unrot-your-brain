//  Router.swift
//  UnrotMac
//
//  Which list the window is on, which card has its focus, and which sheet it is
//  showing. Shared rather than held in a view, so the menu bar, a notification
//  and the popover's "Show the moment…" can ask the window for any
//  of them.

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
    /// The card the keys and the Gap menu act on. Nil, or a card no longer on
    /// the list, reads as the list's first card.
    var focusedGap: String?
    var moment: Moment?
    var check: CheckRequest?

    /// The focused card of the list on screen. Closed has chips, not cards, so
    /// nothing there takes focus.
    func focusedConcept(in store: SurfaceStore) -> Concept? {
        let bucket = list ?? .open
        guard bucket != .closed else { return nil }
        let cards = store.concepts(in: bucket)
        return cards.first { $0.conceptId == focusedGap } ?? cards.first
    }
}
