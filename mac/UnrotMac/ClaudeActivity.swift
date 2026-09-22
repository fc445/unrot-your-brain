//  ClaudeActivity.swift
//  UnrotMac
//
//  Whether Claude Code is being used right now, read from the only evidence
//  unrot is allowed to read: the modification times of its transcripts.
//
//  Read-only, like everything else that touches ~/.claude. This lists and
//  stats; it never opens a file, and nothing in unrot ever writes there.

import Foundation

enum ClaudeActivity {
    static var projects: URL {
        FileManager.default.homeDirectoryForCurrentUser.appending(path: ".claude/projects")
    }

    /// True if any transcript was written within `window` of `now`.
    ///
    /// A notification landing mid-session is the interrupting version of this
    /// product; a session that has written in the last ten minutes is treated
    /// as live. Stops at the first recent file rather than walking them all.
    static func isLive(within window: TimeInterval = 600, now: Date = Date()) -> Bool {
        guard let files = FileManager.default.enumerator(
            at: projects,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles, .skipsPackageDescendants]
        ) else { return false }

        for case let url as URL in files where url.pathExtension == "jsonl" {
            let modified = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate
            if let modified, now.timeIntervalSince(modified) < window { return true }
        }
        return false
    }
}
