//  Snapshots.swift
//  UnrotMac
//
//  Debug builds only: draw every screen offscreen and write each to a PNG, so
//  the UI can be looked at -- and compared with the design canvas -- without a
//  display, screen recording, or anyone at the keyboard.
//
//      UNROT_SNAPSHOTS=<out dir> \
//      UNROT_SNAPSHOT_HOMES="full=/tmp/us/full.sock;clean=/tmp/us/clean.sock;…" \
//      Unrot.app/Contents/MacOS/Unrot
//
//  Each home is a running core on a socket; mac/snapshots/run.sh builds and
//  starts them. The app never spawns its own core, shows its window, or adds
//  its menu-bar item in this mode, and exits when done.
//
//  Drawing is in-process -- an offscreen window and `cacheDisplay` -- which is
//  why no screen permission is involved: nothing reads the screen.

#if DEBUG
import AppKit
import SwiftUI
import UnrotKit

@MainActor
enum Snapshots {
    static var requested: Bool { ProcessInfo.processInfo.environment["UNROT_SNAPSHOTS"] != nil }

    static func run() async {
        let env = ProcessInfo.processInfo.environment
        let out = URL(filePath: env["UNROT_SNAPSHOTS"]!)
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        var homes: [(String, String)] = []
        for pair in (env["UNROT_SNAPSHOT_HOMES"] ?? "").split(separator: ";") {
            let parts = pair.split(separator: "=", maxSplits: 1).map(String.init)
            if parts.count == 2 { homes.append((parts[0], parts[1])) }
        }
        homes.append(("failed", "/tmp/unrot-no-core-here.sock"))

        for (name, socket) in homes {
            let kit = await Kit(socket: socket, up: name != "failed")
            await shoot("main-\(name)", size: NSSize(width: 1040, height: 760), settle: .milliseconds(900)) {
                RootView(core: kit.core, store: kit.store, quick: kit.quick, watcher: kit.watcher, router: Router())
            }
            if ["full", "clean", "failed"].contains(name) {
                await shoot("popover-\(name)") {
                    TrayPopover(store: kit.store, core: kit.core, quick: kit.quick, watcher: kit.watcher, router: Router(), openMain: {})
                }
            }
            guard name == "full" else { continue }
            await shootFull(kit)
        }

        if let (_, socket) = homes.first(where: { $0.0 == "empty" }) {
            let kit = await Kit(socket: socket, up: true)
            for step in 1...3 {
                await shoot("firstrun-\(step)", size: NSSize(width: 1040, height: 700)) {
                    FirstRunView(onboarding: Onboarding(), watcher: kit.watcher, settings: ModelSettings(), step: step)
                }
            }
        }
        await shoot("tray-glyphs", settle: .milliseconds(50)) { GlyphSheet() }
        exit(0)
    }

    /// Everything that needs a populated store.
    private static func shootFull(_ kit: Kit) async {
        let store = kit.store
        await shoot("triage-full") {
            TriageView(store: store, quick: kit.quick, finished: {})
        }

        let capture = CaptureModel(store: store)
        capture.reset(selection: "backpressure handling", from: "Safari")
        await shoot("capture-selection") { CaptureView(model: capture, done: {}) }
        let blank = CaptureModel(store: store)
        blank.reset(selection: nil, from: nil)
        await shoot("capture-blank") { CaptureView(model: blank, done: {}) }
        if let filed = submitted(decision: "existing", name: "backpressure") {
            let model = CaptureModel(store: store)
            model.reset(selection: "flow control", from: "Safari")
            model.showForSnapshot(.filed(filed))
            await shoot("capture-filed-match") { CaptureView(model: model, done: {}) }
        }
        let refused = CaptureModel(store: store)
        refused.reset(selection: "asdkjh", from: nil)
        refused.showForSnapshot(.failed("That reads as keyboard noise rather than a term. Try the term, or roughly what it sounded like."))
        await shoot("capture-refused") { CaptureView(model: refused, done: {}) }

        if let learning = store.concepts(in: .learning).first {
            // Opens on the answer already on record, so this is the graded view.
            await shoot("check-graded", size: NSSize(width: 1000, height: 660), settle: .milliseconds(900)) {
                CheckSheet(conceptId: learning.conceptId, store: store, onClose: {})
            }
        }
        if let waiting = store.concepts(in: .open).first(where: { $0.explanations.isEmpty }) {
            await shoot("check-fresh", size: NSSize(width: 1000, height: 660), settle: .milliseconds(900)) {
                CheckSheet(conceptId: waiting.conceptId, store: store, onClose: {})
            }
        }
        var taken = Set<String>()
        for concept in store.surface?.concepts ?? [] {
            guard let encounter = concept.lead else { continue }
            let tag = encounter.resolvable ? "resolvable" : "unresolvable"
            guard taken.insert(tag).inserted else { continue }
            await shoot("moment-\(tag)", size: NSSize(width: 960, height: 640), settle: .milliseconds(900)) {
                MomentSheet(
                    request: .init(conceptId: concept.conceptId, encounterId: encounter.encounterId),
                    store: store, quick: kit.quick, onClose: {}
                )
            }
        }

        let settings = ModelSettings()
        await shoot("settings-capture", size: NSSize(width: 760, height: 620), settle: .milliseconds(900)) {
            CapturePane(watcher: kit.watcher).frame(width: 760, height: 620)
        }
        let regenerator = Regenerator(client: kit.client, store: store)
        await shoot("settings-model", size: NSSize(width: 760, height: 620), settle: .milliseconds(1200)) {
            ModelPane(settings: settings, client: kit.client, watcher: kit.watcher, regenerator: regenerator,
                      restartCore: {}, showPlan: {})
                .frame(width: 760, height: 620)
        }
        await shoot("settings-notifications", size: NSSize(width: 760, height: 420)) {
            NotificationsPane(notifier: Notifier(store: store, quick: kit.quick, openMain: {}, openTriage: {}))
        }
        await shoot("settings-advanced", size: NSSize(width: 760, height: 560), settle: .milliseconds(900)) {
            RegeneratePane(regenerator: regenerator).frame(width: 760, height: 560)
        }
    }

    // MARK: - Drawing

    /// Light and dark, each to its own file.
    private static func shoot<V: View>(
        _ name: String,
        size: NSSize? = nil,
        settle: Duration = .milliseconds(300),
        @ViewBuilder _ content: () -> V
    ) async {
        for (suffix, appearance) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
            // A sized shot is pinned to its size, as a real window pins its
            // content; views that fill their window would otherwise grow to
            // whatever the offscreen window lets them.
            let hosting = NSHostingView(rootView: content().frame(width: size?.width, height: size?.height))
            let window = NSWindow(
                contentRect: NSRect(origin: .zero, size: size ?? NSSize(width: 400, height: 300)),
                styleMask: [.borderless], backing: .buffered, defer: false
            )
            window.appearance = NSAppearance(named: appearance)
            window.contentView = hosting
            window.setFrameOrigin(NSPoint(x: -30000, y: -30000))
            window.orderFrontRegardless()
            if size == nil { window.setContentSize(hosting.fittingSize) }
            try? await Task.sleep(for: settle)
            if size == nil { window.setContentSize(hosting.fittingSize) }
            hosting.layoutSubtreeIfNeeded()
            hosting.display()

            if let rep = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) {
                hosting.cacheDisplay(in: hosting.bounds, to: rep)
                let file = URL(filePath: ProcessInfo.processInfo.environment["UNROT_SNAPSHOTS"]!)
                    .appending(path: "\(name)-\(suffix).png")
                try? rep.representation(using: .png, properties: [:])?.write(to: file)
            }
            window.orderOut(nil)
        }
    }

    // MARK: - Fixtures

    @MainActor
    final class Kit {
        let core = CoreProcess()
        let client: UnrotClient
        let store: SurfaceStore
        let quick: QuickAccept
        let watcher: Watcher

        init(socket: String, up: Bool) async {
            client = UnrotClient(socketPath: socket, timeout: 10)
            store = SurfaceStore(client: client)
            quick = QuickAccept(store: store)
            watcher = Watcher(client: client, store: store, core: core)
            core.showForSnapshot(up ? .up(version: "0.1.0", rawOpen: true)
                                    : .down(reason: "Could not reach the unrot core.", attempt: 3))
            await store.load()
            await watcher.refreshQueue()
        }
    }

    private static func submitted(decision: String, name: String) -> Submitted? {
        decode(Submitted.self, [
            "encounter_id": "e-snapshot", "concept_id": "c-\(name)", "canonical_name": name,
            "decision": decision,
            "reasoning": "“Flow control” here means slowing the sender when the receiver falls behind — the same idea as backpressure.",
            "judgment_event_id": "ev-snapshot", "decided_without_model": false,
            "model_unavailable": NSNull(), "concept": NSNull(), "counts": [String: Int](),
        ])
    }

    private static func gradedFor(_ explanation: Explanation, concept: Concept) -> Graded? {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        guard let e = try? encoder.encode(explanation),
              let object = try? JSONSerialization.jsonObject(with: e)
        else { return nil }
        return decode(Graded.self, ["explanation": object, "concept": NSNull(), "counts": [String: Int](), "graded": true])
    }

    private static func decode<T: Decodable>(_ type: T.Type, _ object: [String: Any]) -> T? {
        guard let data = try? JSONSerialization.data(withJSONObject: object) else { return nil }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try? decoder.decode(T.self, from: data)
    }
}

/// The five ring states on a menu-bar-like strip, at the size they ship.
private struct GlyphSheet: View {
    private let states: [(TrayState, Int, String)] = [
        (.clean, 0, "clean"), (.waiting(3), 3, "waiting"), (.analysing, 3, "analysing"),
        (.paused, 3, "paused"), (.coreDown, 0, "core down"),
    ]

    var body: some View {
        HStack(spacing: 22) {
            ForEach(states, id: \.2) { state, count, label in
                VStack(spacing: 6) {
                    HStack(spacing: 2) {
                        Image(nsImage: TrayGlyph.image(for: state))
                            .renderingMode(.template)
                        if count > 0 {
                            Text("\(count)").font(.system(size: 12, weight: .medium).monospacedDigit())
                        }
                    }
                    .padding(.horizontal, 8)
                    .frame(height: 24)
                    .background(.bar, in: RoundedRectangle(cornerRadius: 5))
                    Text(label).font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
        }
        .padding(20)
        .background(Color.paper)
    }
}
#endif
