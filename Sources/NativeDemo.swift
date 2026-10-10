import AppKit
import WebKit

/// Genuine production-window frames and RPC actions, confined to a throwaway library.
@MainActor enum NativeDemo {
    static func run(_ controller: LibraryWindowController, output: URL) async {
        var frames: [JSON] = [], events: [JSON] = [], recording = true
        var recorder: Task<Void, Error>?
        let start = Date()
        func event(_ title: String) { events.append(["title": title, "seconds": Date().timeIntervalSince(start)]) }
        do {
            guard NSApp.activationPolicy() == .prohibited else { throw EngineFailure("offscreen_required", "演示必须离屏运行。") }
            try FileManager.default.createDirectory(at: output.appendingPathComponent("frames"), withIntermediateDirectories: true)
            controller.window?.setContentSize(NSSize(width: 1320, height: 850))
            let content: JSON = ["title": "AI 项目复盘", "subtitle": "从一份成果，走到自己的理解。", "scope": "虚构演示项目", "pages": [
                ["id": "overview", "title": "整体结构", "summary": "先看整体，再顺着章节理解细节。", "blocks": [
                    ["id": "body", "type": "prose", "text": "一个清楚的项目，可以从输入、处理和结果三个部分开始理解。把事实、判断和下一步分开保存，后续阅读就能接着上次的思路。"],
                    ["id": "steps", "type": "steps", "items": [["title": "整理输入", "text": "保留来源，明确要理解的问题。"], ["title": "解释取舍", "text": "把做法、依据和边界放在一起。"], ["title": "保存认识", "text": "补充批注，让成果成为自己的知识。"]]]]],
                ["id": "choices", "title": "关键取舍", "summary": "结论旁边，也留住作出判断的理由。", "blocks": [["id": "choices-body", "type": "prose", "text": "这次选择本地保存，因为知识需要可以离线阅读，也需要保留人工修改。生成只是起点，阅读、编辑与版本记录共同构成完整流程。"]]],
                ["id": "actions", "title": "后续行动", "summary": "读明白之后，再决定下一步。", "blocks": [["id": "actions-body", "type": "prose", "text": "核对原始来源，补充自己的判断，保存并定期回读。"]]]]]
            let result = try await controller.engine.call("build", ["content": content, "kind": "portal", "project": "个人知识", "tags": ["项目复盘", "方法"]])
            guard let document = (result["document"] as? JSON).flatMap(KnowledgeDocument.init) else { throw EngineFailure("demo_fixture", "演示成果未创建。") }
            for title in ["读书笔记：如何提出好问题", "学习记录：把概念连起来", "工作方法：让下一步更清楚"] {
                _ = try await controller.engine.call("build", ["content": ["title": title, "pages": [["id": "note", "title": title, "blocks": [["id": "note-body", "type": "prose", "text": "这是一份虚构示例，用来展示知识分类与阅读。"]]]]], "kind": "report", "project": "日常积累"])
            }
            await controller.refresh(); await controller.open(documentID: document.id, pageID: "overview")
            controller.setInspectorVisible(false)
            try await NativeSelfTest.waitForDocument(controller)
            try await NativeSelfTest.snapshot(controller, to: output.appendingPathComponent("library.png"), appearance: .aqua)
            recorder = Task { @MainActor in
                while recording && Date().timeIntervalSince(start) < 60 {
                    let timestamp = Date().timeIntervalSince(start)
                    let filename = String(format: "frames/%05d.png", frames.count)
                    try await NativeSelfTest.snapshot(controller, to: output.appendingPathComponent(filename), appearance: .aqua)
                    frames.append(["path": filename, "seconds": timestamp])
                    try await Task.sleep(nanoseconds: 200_000_000)
                }
            }
            event("整体阅读：沿着章节理解成果")
            try await Task.sleep(nanoseconds: 1_000_000_000)
            await controller.open(documentID: document.id, pageID: "choices")
            try await NativeSelfTest.waitForDocument(controller)
            try await Task.sleep(nanoseconds: 2_000_000_000)
            controller.searchField.stringValue = "本地保存"; await controller.refresh()
            try await Task.sleep(nanoseconds: 2_000_000_000)
            controller.searchField.stringValue = ""; await controller.refresh()
            await controller.open(documentID: document.id, pageID: "overview")
            try await NativeSelfTest.waitForDocument(controller)
            event("直接编辑：把自己的认识写进正文")
            _ = try await NativeSelfTest.js(controller.reader, "window.LumenReader.setEditing(true);const p=document.querySelector('[data-lumen-source=\"/pages/0/blocks/0/text\"]');p.textContent='一个清楚的项目，可以从输入、处理和结果三个部分开始理解。我的补充：每个结论都要能回到来源。';p.dispatchEvent(new Event('input',{bubbles:true}));window.LumenReader.addComment(p.dataset.feedbackBlock,'把结论和依据一起保存。');true")
            try await Task.sleep(nanoseconds: 3_000_000_000)
            event("保存重开：正文与批注一起进入新版本")
            try await NativeSelfTest.saveAndReopen(controller)
            controller.setInspectorVisible(true)
            controller.notes.string = "下次回读：重点核对判断的依据。"; controller.captureMetadataDraft()
            let revision = controller.selectedDocument?.metadataRevision ?? 0
            controller.saveMetadata(); try await NativeSelfTest.waitForMetadata(controller, after: revision)
            await controller.loadVersions()
            try await NativeSelfTest.snapshot(controller, to: output.appendingPathComponent("editing.png"), appearance: .aqua)
            try await Task.sleep(nanoseconds: 3_000_000_000)
            event("版本恢复：历史一直保留")
            if let current = controller.selectedDocument { await controller.restore(version: 1, document: current) }
            try await NativeSelfTest.waitForDocument(controller)
            await controller.loadVersions()
            try await Task.sleep(nanoseconds: 3_000_000_000)
            recording = false
            try await recorder?.value
            let actual = try await controller.engine.call("get", ["document_id": document.id])
            let version = (actual["document"] as? JSON)?["version"] as? Int ?? 0
            let quiet = NSApp.activationPolicy() == .prohibited && !NSApp.isActive && NSApp.windows.allSatisfy { !$0.isVisible && !$0.isKeyWindow }
            guard quiet, version >= 3, frames.count > 20 else { throw EngineFailure("demo_incomplete", "演示没有完整记录真实保存与恢复。") }
            let report: JSON = ["ok": true, "method": "production-native-offscreen-capture", "physical_input": false,
                "fixture": "虚构示例数据；独立临时知识库", "quiet": quiet, "version": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") ?? "",
                "build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") ?? "", "frames": frames, "events": events,
                "duration_seconds": Date().timeIntervalSince(start), "saved_and_restored_version": version]
            try LumenPaths.data(report).write(to: output.appendingPathComponent("capture.json"), options: .atomic)
            print(try LumenPaths.string(report)); fflush(stdout)
            NSApp.stop(nil); exit(0)
        } catch {
            recording = false; recorder?.cancel()
            let result: JSON = ["ok": false, "error": error.localizedDescription]
            if let value = try? LumenPaths.string(result) { print(value); fflush(stdout) }
            NSApp.stop(nil); exit(1)
        }
    }
}
