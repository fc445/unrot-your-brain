//  LangSmithSettings.swift
//  UnrotMac
//
//  Dev-channel builds only: whether the core traces its model calls to
//  LangSmith, and with which key. The core needs nothing for this -- it talks
//  to models through LangChain, which traces whenever LANGSMITH_TRACING and
//  LANGSMITH_API_KEY are in its environment -- so this is only the environment.
//
//  The key defaults to the one the build was given (UNROT_DEV_LANGSMITH_API_KEY,
//  recorded in Info.plist as UnrotLangSmithKey). A key saved here wins over it,
//  which is what a Debug build out of Xcode, built without one, needs. A prod
//  build compiles none of this, and packaging/dmg.sh refuses one that carries a
//  key anyway.

#if DEV_FEATURES
import Foundation
import Observation

@MainActor
@Observable
final class LangSmithSettings {
    enum KeySource {
        case saved, build, none
        var label: String {
            switch self {
            case .saved: "Saved in Keychain"
            case .build: "From the build"
            case .none: "None"
            }
        }
    }

    static let keyAccount = "LANGSMITH_API_KEY"
    static let defaultProject = "unrot-dev"

    /// Empty when the build was not given one.
    static let buildKey = (Bundle.main.object(forInfoDictionaryKey: "UnrotLangSmithKey") as? String)?
        .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

    var enabled: Bool { didSet { save(); changed = true } }
    /// Empty means `defaultProject`.
    var project: String { didSet { save(); changed = true } }
    private(set) var hasSavedKey: Bool

    /// Settings differ from what the running core was started with.
    private(set) var changed = false

    private let defaults = UserDefaults.standard

    init() {
        enabled = defaults.bool(forKey: "UnrotLangSmithTracing")
        project = defaults.string(forKey: "UnrotLangSmithProject") ?? ""
        hasSavedKey = Keychain.has(Self.keyAccount)
    }

    var keySource: KeySource {
        if hasSavedKey { return .saved }
        return Self.buildKey.isEmpty ? .none : .build
    }

    func saveKey(_ key: String) {
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, Keychain.write(trimmed, for: Self.keyAccount) else { return }
        hasSavedKey = true
        changed = true
    }

    func removeKey() {
        Keychain.delete(Self.keyAccount)
        hasSavedKey = false
        changed = true
    }

    /// Called once the core has been restarted with these settings.
    func applied() { changed = false }

    /// What to add to the core's environment. Off says so explicitly, so a
    /// `LANGSMITH_TRACING=true` in the repo `.env` -- which a Debug build's
    /// development core loads -- cannot switch tracing on behind the toggle.
    func environment() -> [String: String] {
        let key = hasSavedKey ? Keychain.read(Self.keyAccount) : Self.buildKey
        guard enabled, let key, !key.isEmpty else { return ["LANGSMITH_TRACING": "false"] }
        let project = project.trimmingCharacters(in: .whitespaces)
        return [
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_API_KEY": key,
            "LANGSMITH_PROJECT": project.isEmpty ? Self.defaultProject : project,
        ]
    }

    private func save() {
        defaults.set(enabled, forKey: "UnrotLangSmithTracing")
        defaults.set(project, forKey: "UnrotLangSmithProject")
    }
}
#endif
