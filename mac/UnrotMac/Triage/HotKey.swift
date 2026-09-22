//  HotKey.swift
//  UnrotMac
//
//  One system-wide shortcut, registered through Carbon.
//
//  `RegisterEventHotKey` rather than an `NSEvent` global monitor, for the same
//  reason the design gives for the capture shortcut: a global monitor sees
//  every keystroke in every app, so macOS makes it ask for Accessibility
//  permission. A registered hotkey sees only its own chord and asks for
//  nothing. A feature that needs one key combination should not need the
//  ability to read all of them.

import AppKit
import Carbon.HIToolbox

@MainActor
final class HotKey {
    private var hotKey: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private let action: @MainActor () -> Void

    /// `keyCode` is a virtual key (`kVK_*`); `modifiers` are Carbon masks
    /// (`cmdKey`, `optionKey`, ...). Nil if the chord is already taken.
    init?(keyCode: Int, modifiers: Int, action: @escaping @MainActor () -> Void) {
        self.action = action

        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let context = Unmanaged.passUnretained(self).toOpaque()
        let installed = InstallEventHandler(
            GetApplicationEventTarget(),
            { _, _, context in
                guard let context else { return noErr }
                let hotKey = Unmanaged<HotKey>.fromOpaque(context).takeUnretainedValue()
                // Carbon calls back on the main thread; say so to the compiler
                // rather than hopping queues and adding a frame of latency.
                MainActor.assumeIsolated { hotKey.action() }
                return noErr
            },
            1, &spec, context, &handler
        )
        guard installed == noErr else { return nil }

        let id = EventHotKeyID(signature: OSType(0x756E_7274), id: 1)  // 'unrt'
        let registered = RegisterEventHotKey(
            UInt32(keyCode), UInt32(modifiers), id, GetApplicationEventTarget(), 0, &hotKey
        )
        guard registered == noErr else {
            if let handler { RemoveEventHandler(handler) }
            return nil
        }
    }

    func unregister() {
        if let hotKey { UnregisterEventHotKey(hotKey) }
        if let handler { RemoveEventHandler(handler) }
        hotKey = nil
        handler = nil
    }
}
