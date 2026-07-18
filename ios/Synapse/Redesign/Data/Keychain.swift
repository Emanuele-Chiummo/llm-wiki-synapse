import Foundation
import Security

/// Minimal Keychain wrapper for the one secret the redesign stores: the
/// per-device scoped API token (PF-AUTH-1). **Hard security requirement**
/// (Track iOS 2.1 Fase B): the auth token is never written to UserDefaults or
/// any plaintext store — only here, in the Keychain, as a generic password.
///
/// Keyed by a stable account string so a fresh token overwrites the previous
/// one. Access is `WhenUnlockedThisDeviceOnly` — the token never leaves the
/// device (no iCloud Keychain sync) and is unreadable while the phone is locked.
enum Keychain {
    /// Namespaced service so Synapse's items never collide with other apps'.
    private static let service = "ai.synapse.mobile.secrets"

    /// Store (or overwrite) a secret for `account`. Passing `nil`/empty deletes it.
    @discardableResult
    static func set(_ value: String?, account: String) -> Bool {
        guard let value, !value.isEmpty else { return delete(account: account) }
        guard let data = value.data(using: .utf8) else { return false }

        // Delete any existing item first so we always end up with exactly one.
        delete(account: account)

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        ]
        return SecItemAdd(query as CFDictionary, nil) == errSecSuccess
    }

    /// Read the secret for `account`, or nil if absent.
    static func get(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let str = String(data: data, encoding: .utf8)
        else { return nil }
        return str
    }

    /// Remove the secret for `account`. Returns true if it no longer exists.
    @discardableResult
    static func delete(account: String) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
    }

    // Stable account keys.
    enum Account {
        static let apiToken = "apiToken"
        static let cfAccessSecret = "cfAccessClientSecret"
    }
}
