//  CaptureService.swift
//  UnrotMac
//
//  The Services provider behind "Add to unrot".
//
//  It reads the selection off the pasteboard it is handed and nothing else.
//  macOS gives a service exactly what the user selected -- not the document,
//  not the page, not the text around it -- and this is the whole reason the
//  feature can exist without Accessibility permission: nothing here can read
//  anything the user did not select and choose to send.

import AppKit

@MainActor
final class CaptureService: NSObject {
    private let open: (_ selection: String, _ from: String?) -> Void

    init(open: @escaping (_ selection: String, _ from: String?) -> Void) {
        self.open = open
    }

    /// Named by `NSMessage` in Info.plist.
    @objc(addToUnrot:userData:error:)
    func addToUnrot(
        _ pasteboard: NSPasteboard,
        userData: String?,
        error: AutoreleasingUnsafeMutablePointer<NSString?>
    ) {
        guard let selection = pasteboard.string(forType: .string)?
            .trimmingCharacters(in: .whitespacesAndNewlines), !selection.isEmpty
        else {
            error.pointee = "There was no text in the selection." as NSString
            return
        }
        open(selection, Self.invokingApp())
    }

    /// The app the selection came from, by name.
    ///
    /// Frontmost at the moment of the call, because a service does not activate
    /// its provider. The app's name only: a document or page title would need
    /// Accessibility to read, which this feature exists not to require. Nil
    /// when the selection came from unrot itself -- "seen in unrot" says nothing.
    private static func invokingApp() -> String? {
        guard let app = NSWorkspace.shared.frontmostApplication,
              app.bundleIdentifier != Bundle.main.bundleIdentifier
        else { return nil }
        return app.localizedName
    }
}
