//  WatchPolicy.swift
//  UnrotKit
//
//  When a finished session is ready to be captured. A pure function, so the
//  quiet-period rule is a test rather than a timing bug.
//
//  The detector judges the human's *next* turn after something is accepted, and
//  mid-session that turn does not exist yet. So a transcript is only taken once
//  it has been quiet for a while -- and only once its whole project has, because
//  Claude Code still writing anywhere in a project means someone is still
//  working in it.

import Foundation

public enum WatchPolicy {
    /// The default quiet period, from PR-29.
    public static let defaultQuiet: TimeInterval = 10 * 60

    /// Transcripts whose own last write, and every sibling's, is at least
    /// `quiet` ago. `changes` maps a transcript path to when it last changed.
    public static func ready(changes: [String: Date], now: Date, quiet: TimeInterval = defaultQuiet) -> [String] {
        var liveProjects = Set<String>()
        for (path, changed) in changes where now.timeIntervalSince(changed) < quiet {
            liveProjects.insert(project(of: path))
        }
        return changes.keys
            .filter { !liveProjects.contains(project(of: $0)) }
            .sorted()
    }

    /// A project is the directory a transcript sits in -- one per repo, the way
    /// Claude Code lays out `~/.claude/projects`.
    public static func project(of path: String) -> String {
        (path as NSString).deletingLastPathComponent
    }
}
