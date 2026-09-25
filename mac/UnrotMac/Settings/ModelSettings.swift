//  ModelSettings.swift
//  UnrotMac
//
//  Which endpoint and model the core should use, and the key -- turned into the
//  environment the core is spawned with.

import Foundation
import Observation

@MainActor
@Observable
final class ModelSettings {
    enum Endpoint: String, CaseIterable, Identifiable {
        case hosted, local, custom
        var id: String { rawValue }
        var label: String {
            switch self {
            case .hosted: "Hosted — OpenRouter"
            case .local: "On this Mac"
            case .custom: "Custom endpoint"
            }
        }
    }

    /// How long a reasoning model may think before it answers. OpenRouter's
    /// `reasoning.effort`; ignored by models that do not reason, and never sent
    /// to a local server.
    enum Effort: String, CaseIterable, Identifiable {
        /// Nothing set here: the core's default, which is low.
        case core = ""
        case low, medium, high
        /// Send nothing, and let the model think as long as it likes.
        case model = "default"
        var id: String { rawValue }
        var label: String {
            switch self {
            case .core: "Default (low)"
            case .low: "Low"
            case .medium: "Medium"
            case .high: "High"
            case .model: "Model decides"
            }
        }
    }

    static let keyAccount = "OPENROUTER_API_KEY"

    var endpoint: Endpoint { didSet { save(); changed = true } }
    var localURL: String { didSet { save(); changed = true } }
    var customURL: String { didSet { save(); changed = true } }
    /// Empty means the core's own default.
    var model: String { didSet { save(); changed = true } }
    var effort: Effort { didSet { save(); changed = true } }
    /// How many sessions are analysed side by side, and how many parts of one
    /// long session the core sends together (`UNROT_CONCURRENCY`).
    var atOnce: Int { didSet { save(); changed = true } }
    static let defaultAtOnce = 4
    static let atOnceRange = 1...8
    private(set) var hasKey: Bool

    /// Settings differ from what the running core was started with.
    private(set) var changed = false

    private let defaults = UserDefaults.standard

    init() {
        endpoint = Endpoint(rawValue: defaults.string(forKey: "UnrotEndpoint") ?? "") ?? .hosted
        localURL = defaults.string(forKey: "UnrotLocalURL") ?? "http://localhost:11434/v1"
        customURL = defaults.string(forKey: "UnrotCustomURL") ?? ""
        model = defaults.string(forKey: "UnrotModel") ?? ""
        effort = Effort(rawValue: defaults.string(forKey: "UnrotReasoningEffort") ?? "") ?? .core
        let stored = defaults.integer(forKey: "UnrotConcurrency")
        atOnce = stored == 0 ? Self.defaultAtOnce : min(max(stored, Self.atOnceRange.lowerBound), Self.atOnceRange.upperBound)
        hasKey = Keychain.has(Self.keyAccount)
    }

    func saveKey(_ key: String) {
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, Keychain.write(trimmed, for: Self.keyAccount) else { return }
        hasKey = true
        changed = true
    }

    func removeKey() {
        Keychain.delete(Self.keyAccount)
        hasKey = false
        changed = true
    }

    /// What applies. A local server answers one request at a time, so more
    /// would only queue there -- and each queued request would wait against the
    /// app's timeout.
    var concurrency: Int { endpoint == .local ? 1 : atOnce }

    /// Called once the core has been restarted with these settings.
    func applied() { changed = false }

    /// What to add to the core's environment. Only ever *added*: a variable
    /// already present in the app's own environment wins, so the precedence
    /// `unrot.resolver env` describes still holds.
    func environment() -> [String: String] {
        var env: [String: String] = [:]
        switch endpoint {
        case .hosted:
            break  // the core's default is OpenRouter
        case .local:
            env["UNROT_BASE_URL"] = localURL
        case .custom:
            if !customURL.isEmpty { env["UNROT_BASE_URL"] = customURL }
        }
        if !model.trimmingCharacters(in: .whitespaces).isEmpty {
            env["UNROT_MODEL"] = model.trimmingCharacters(in: .whitespaces)
        }
        switch effort {
        case .core: break
        // Empty tells the core to send no effort at all.
        case .model: env["UNROT_REASONING_EFFORT"] = ""
        default: env["UNROT_REASONING_EFFORT"] = effort.rawValue
        }
        env["UNROT_CONCURRENCY"] = String(concurrency)
        if endpoint == .local {
            // A local server wants a key to be present and ignores its value.
            // Never the real one: handing an OpenRouter key to whatever is
            // listening on localhost buys nothing and risks something.
            env["OPENROUTER_API_KEY"] = "local"
        } else if let key = Keychain.read(Self.keyAccount) {
            env["OPENROUTER_API_KEY"] = key
        }
        return env
    }

    private func save() {
        defaults.set(endpoint.rawValue, forKey: "UnrotEndpoint")
        defaults.set(localURL, forKey: "UnrotLocalURL")
        defaults.set(customURL, forKey: "UnrotCustomURL")
        defaults.set(model, forKey: "UnrotModel")
        defaults.set(effort.rawValue, forKey: "UnrotReasoningEffort")
        defaults.set(atOnce, forKey: "UnrotConcurrency")
    }
}
