//  RootView.swift
//  UnrotMac
//
//  A port of ui/src/App.tsx, and it follows that file closely on purpose --
//  down to which decisions are not made here.
//
//  Nothing in this view computes a bucket, a surface state, a headline or a
//  next step. Every one of those arrives from `/api/surface` already decided,
//  because the rule that decides them has to stay next to `derive_state` in
//  `api/read.py`. What this file decides is layout.

import SwiftUI
import UnrotKit

struct RootView: View {
    let core: CoreProcess
    @Bindable var store: SurfaceStore

    var body: some View {
        ZStack {
            Color.paper.ignoresSafeArea()
            content
        }
        .task {
            // The first paint waits for the core to answer rather than showing
            // a failure the supervisor is already fixing.
            await store.load()
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard core.status.isUp else { continue }
                await store.load()
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Masthead(store: store, core: core)

                if let empty = store.emptyState {
                    EmptyStateView(
                        state: empty,
                        headline: headline(for: empty),
                        detail: detail(for: empty)
                    )
                    .padding(.top, 8)
                } else if let surface = store.surface {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(surface.headline)
                            .font(.display(26))
                            .foregroundStyle(Color.inkPrimary)
                        Text(surface.detail)
                            .font(.system(size: 13))
                            .foregroundStyle(Color.inkSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(.top, 18)

                    if surface.fixtures > 0 {
                        FixturesNotice(count: surface.fixtures).padding(.top, 14)
                    }

                    ForEach(store.populated) { section in
                        SectionView(section: section, store: store)
                            .padding(.top, 26)
                    }
                } else if !store.hasLoadedOnce {
                    Text("Reading the log…")
                        .font(.system(size: 13))
                        .foregroundStyle(Color.inkFaint)
                        .padding(.top, 40)
                }
            }
            .padding(.horizontal, 28)
            .padding(.bottom, 48)
            .frame(maxWidth: 780, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
    }

    // The server writes the words for every state it sends. `failed` is the one
    // it cannot send -- a response saying "I failed" is a response -- so it is
    // the one state whose words live here.
    private func headline(for state: SurfaceState) -> String {
        if state == .failed { return "Something broke" }
        return store.surface?.headline ?? ""
    }

    private func detail(for state: SurfaceState) -> String {
        if state == .failed { return store.failure ?? "The core did not answer." }
        return store.surface?.detail ?? ""
    }
}

private struct Masthead: View {
    let store: SurfaceStore
    let core: CoreProcess

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            HStack(spacing: 6) {
                Text("unrot").font(.display(21))
                Text("/ your brain")
                    .font(.display(21))
                    .foregroundStyle(Color.inkFaint)
            }
            Spacer()
            if let capture = store.surface?.capture, let surface = store.surface {
                HStack(spacing: 16) {
                    Tally(value: capture.sessionsAnalysed, of: capture.sessions, label: "sessions examined")
                    Tally(value: surface.count(.open), label: "waiting")
                    Tally(value: surface.count(.closed), label: "closed")
                }
            }
        }
        .padding(.top, 22)
        .padding(.bottom, 14)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Color.rule).frame(height: 1)
        }
        .overlay(alignment: .bottomLeading) {
            CoreBadge(core: core).offset(y: 22)
        }
    }
}

private struct Tally: View {
    var value: Int
    var of: Int?
    var label: String

    var body: some View {
        HStack(spacing: 4) {
            Text(of.map { "\(value) of \($0)" } ?? "\(value)")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Color.inkPrimary)
            Text(label)
                .font(.system(size: 12))
                .foregroundStyle(Color.inkFaint)
        }
    }
}

/// Only ever shown when the core is not up. A healthy core is the normal case
/// and says nothing about itself.
private struct CoreBadge: View {
    let core: CoreProcess

    var body: some View {
        switch core.status {
        case .up:
            EmptyView()
        case .starting:
            badge("Starting the core…", tint: .inkFaint, wash: .sunk)
        case .foreign:
            badge("Another copy of unrot is running.", tint: .bucketOpen, wash: .bucketOpenBG)
        case .down(let reason, let attempt):
            badge("Core not answering — retrying (\(attempt)). \(reason)", tint: .alarm, wash: .alarmBG)
        case .stopped(let reason):
            badge("Core stopped. \(reason)", tint: .alarm, wash: .alarmBG)
        }
    }

    private func badge(_ text: String, tint: Color, wash: Color) -> some View {
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(tint)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(wash, in: Capsule())
    }
}

/// Seeded data must never read as a finding about the user.
private struct FixturesNotice: View {
    let count: Int

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text("fixtures")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Color.inkFaint)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(Color.sunk, in: Capsule())
            Text(
                "\(count) of these events are development fixtures standing in for the "
                + "resolver. Remove them with `python -m unrot.store seed --clear`."
            )
            .font(.system(size: 12))
            .foregroundStyle(Color.inkSoft)
            .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .background(Color.sunk, in: RoundedRectangle(cornerRadius: 8))
    }
}

private struct SectionView: View {
    let section: BucketSection
    let store: SurfaceStore

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(section.title)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Color.inkPrimary)
                Text("\(store.surface?.count(section.bucket) ?? 0)")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(section.bucket.tint)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(section.bucket.wash, in: Capsule())
                Text(section.note)
                    .font(.system(size: 12))
                    .foregroundStyle(Color.inkFaint)
            }

            ForEach(store.concepts(in: section.bucket)) { concept in
                GapCardView(concept: concept, store: store)
            }
        }
    }
}
