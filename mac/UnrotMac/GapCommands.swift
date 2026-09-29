//  GapCommands.swift
//  UnrotMac
//
//  What a card can do, as menu items: the Gap menu in the menu bar, acting on
//  whichever card has the window's focus, and each card's context menu.
//
//  The keys stay the fast way (D, K, the arrows -- see RootView); these are
//  where people look to find out what the app can do, and what Full Keyboard
//  Access and Voice Control can reach. The menu items carry no key equivalents
//  on purpose: a bare-letter equivalent fires even while a text field has
//  focus, and swallowing the D someone types into Add a Gap is worse than a
//  menu that does not show the key.

import AppKit
import SwiftUI
import UnrotKit

/// One concept's actions, whether asked from the menu bar or the card.
@MainActor
struct GapActions {
    let concept: Concept
    let quick: QuickAccept
    let router: Router
    /// Brings the window forward, for actions whose answer is a sheet in it.
    var open: () -> Void = {}

    /// Waiting, and not yet answered: the one question it asks.
    var canAnswer: Bool { concept.bucket == .open && concept.unanswered != nil }
    /// The check, offered where the card offers it: once there is no longer a
    /// question to answer, and until it is closed.
    var canExplain: Bool { !canAnswer && concept.bucket != .closed }
    var canShowMoment: Bool { concept.lead?.sessionId != nil }

    func answer(_ verdict: Verdict) {
        guard canAnswer, let encounter = concept.unanswered else { return }
        Task { await quick.answer(concept: concept, encounter: encounter, verdict) }
    }

    func explain() {
        router.check = .init(conceptId: concept.conceptId)
        open()
    }

    func showMoment() {
        guard let encounter = concept.lead, encounter.sessionId != nil else { return }
        router.moment = .init(conceptId: concept.conceptId, encounterId: encounter.encounterId)
        open()
    }
}

/// The items themselves, shared by the Gap menu and a card's context menu so
/// the two cannot disagree about names or order.
struct GapMenuItems: View {
    let actions: GapActions?

    var body: some View {
        Button("I Didn't Know This", systemImage: "questionmark.circle") { actions?.answer(.confirm) }
            .disabled(!(actions?.canAnswer ?? false))
        Button("I Knew It", systemImage: "checkmark.circle") { actions?.answer(.dismiss) }
            .disabled(!(actions?.canAnswer ?? false))
        Divider()
        Button("Explain It…", systemImage: "text.bubble") { actions?.explain() }
            .disabled(!(actions?.canExplain ?? false))
        Button("Show Moment…", systemImage: "text.quote") { actions?.showMoment() }
            .disabled(!(actions?.canShowMoment ?? false))
    }
}

/// The Gap menu: the items, for the focused card of the list on screen.
struct GapMenu: View {
    let store: SurfaceStore
    let quick: QuickAccept
    let router: Router
    let open: () -> Void

    var body: some View {
        GapMenuItems(actions: router.focusedConcept(in: store).map {
            GapActions(concept: $0, quick: quick, router: router, open: open)
        })
    }
}

/// A card's right-click menu: the same items, and copying the name.
struct GapContextMenu: View {
    let actions: GapActions

    var body: some View {
        GapMenuItems(actions: actions)
        Divider()
        Button("Copy Name", systemImage: "doc.on.doc") {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(actions.concept.name, forType: .string)
        }
    }
}
