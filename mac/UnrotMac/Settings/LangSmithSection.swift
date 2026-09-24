//  LangSmithSection.swift
//  UnrotMac
//
//  Dev-channel builds only: the Developer tab's switch for tracing the core's
//  model calls to LangSmith. The settings themselves are LangSmithSettings.

#if DEV_FEATURES
import SwiftUI

struct LangSmithSection: View {
    @Environment(LangSmithSettings.self) private var langSmith
    let restartCore: () -> Void

    @State private var key = ""

    var body: some View {
        @Bindable var langSmith = langSmith
        Section {
            Toggle("Export generations to LangSmith", isOn: $langSmith.enabled)
                .disabled(langSmith.keySource == .none && !langSmith.enabled)
            TextField("Project", text: $langSmith.project,
                      prompt: Text(LangSmithSettings.defaultProject))
            LabeledContent("Key", value: langSmith.keySource.label)
            HStack {
                SecureField("Key", text: $key,
                            prompt: Text(langSmith.hasSavedKey ? "Replace the saved key" : "Use a different key"))
                    .labelsHidden()
                Button("Save") { langSmith.saveKey(key); key = "" }
                    .disabled(key.trimmingCharacters(in: .whitespaces).isEmpty)
                if langSmith.hasSavedKey {
                    Button(LangSmithSettings.buildKey.isEmpty ? "Remove" : "Use the build's key") {
                        langSmith.removeKey()
                    }
                }
            }
            if langSmith.changed {
                Button("Restart the core to apply") {
                    restartCore()
                    langSmith.applied()
                }
            }
        } header: {
            Text("LangSmith")
        } footer: {
            Text(langSmith.keySource == .none
                 ? "This build was made without a LangSmith key. Save one above to turn tracing on."
                 : "While this is on, every model call the core makes — prompts, transcript excerpts and answers — is sent to LangSmith.")
                .font(.system(size: 11))
                .foregroundStyle(Color.inkFaint)
        }
    }
}
#endif
