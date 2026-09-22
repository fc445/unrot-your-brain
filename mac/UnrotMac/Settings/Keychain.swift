//  Keychain.swift
//  UnrotMac
//
//  The API key, in the login Keychain rather than a file.
//
//  The app hands it to the core it spawns through that process's environment,
//  so the core never touches the Keychain -- which matters, because a second
//  binary reading an item the app created would prompt for permission every
//  time. The CLI does not read it either: a real environment variable and the
//  repo `.env` stay authoritative there, exactly as `unrot.resolver env`
//  describes. The app is one more source, not a replacement.

import Foundation
import Security

enum Keychain {
    private static let service = "com.unrot.mac"

    static func read(_ account: String) -> String? {
        var query = base(account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data
        else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func has(_ account: String) -> Bool {
        var query = base(account)
        query[kSecReturnAttributes as String] = true
        return SecItemCopyMatching(query as CFDictionary, nil) == errSecSuccess
    }

    @discardableResult
    static func write(_ value: String, for account: String) -> Bool {
        let data = Data(value.utf8)
        let update = SecItemUpdate(base(account) as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if update == errSecSuccess { return true }
        var item = base(account)
        item[kSecValueData as String] = data
        item[kSecAttrLabel as String] = "unrot — \(account)"
        return SecItemAdd(item as CFDictionary, nil) == errSecSuccess
    }

    static func delete(_ account: String) {
        SecItemDelete(base(account) as CFDictionary)
    }

    private static func base(_ account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }
}
