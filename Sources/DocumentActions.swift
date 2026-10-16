import AppKit
import WebKit
import UniformTypeIdentifiers

final class DocumentTableView: NSTableView {
    weak var library: LibraryWindowController?
    override func menu(for event: NSEvent) -> NSMenu? {
        let row = self.row(at:convert(event.locationInWindow,from:nil))
        guard row >= 0 else { return nil }
        if !selectedRowIndexes.contains(row) { selectRowIndexes(IndexSet(integer:row),byExtendingSelection:false) }
        return library?.documentMenu(row:row)
    }
    override func keyDown(with event: NSEvent) {
        let item = NSMenuItem(); item.representedObject = ["document_ids":library?.selectedDocumentIDs ?? []]
        if event.keyCode == 36 && event.modifierFlags.intersection([.command,.option,.control]).isEmpty { library?.renameDocument(item); return }
        if event.keyCode == 51 && event.modifierFlags.contains(.command) { library?.trashDocuments(item); return }
        super.keyDown(with:event)
    }
}

@MainActor final class DocumentInformationController: NSWindowController {
    weak var library: LibraryWindowController?
    let documentID: String, pageID: String?, version: Int?
    let stack = NSStackView(), message = NSTextField(wrappingLabelWithString: "正在读取位置…")
    var records: [JSON] = []
    init(library: LibraryWindowController, documentID: String, pageID: String?, version: Int?) {
        self.library = library; self.documentID = documentID; self.pageID = pageID; self.version = version
        let window = NSWindow(contentRect:NSRect(x:0,y:0,width:820,height:640),styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:true)
        window.title = "文档信息与文件位置"; window.isReleasedWhenClosed = false
        super.init(window:window)
        stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 14
        let scroll = NSScrollView(); scroll.hasVerticalScroller = true; scroll.documentView = stack
        scroll.translatesAutoresizingMaskIntoConstraints = false; stack.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(scroll)
        NSLayoutConstraint.activate([scroll.leadingAnchor.constraint(equalTo:window.contentView!.leadingAnchor,constant:20),scroll.trailingAnchor.constraint(equalTo:window.contentView!.trailingAnchor,constant:-20),scroll.topAnchor.constraint(equalTo:window.contentView!.topAnchor,constant:20),scroll.bottomAnchor.constraint(equalTo:window.contentView!.bottomAnchor,constant:-20),stack.leadingAnchor.constraint(equalTo:scroll.contentView.leadingAnchor),stack.topAnchor.constraint(equalTo:scroll.contentView.topAnchor),stack.widthAnchor.constraint(equalTo:scroll.contentView.widthAnchor)])
        stack.addArrangedSubview(message); message.isSelectable = true; window.center()
    }
    required init?(coder:NSCoder) { fatalError() }
    func load() async {
        guard let library else { return }
        do {
            var params: JSON = ["document_id":documentID]; if let pageID { params["page_id"] = pageID }; if let version { params["version"] = version }
            let result = try await library.engine.call("paths",params)
            message.stringValue = "\(result["title"] as? String ?? "文档")\n查看版本：v\(result["version"] as? Int ?? 0) · \(result["page_count"] as? Int ?? 0) 页 · 全部版本占用 \(ByteCountFormatter.string(fromByteCount:Int64(result["storage_bytes"] as? Int ?? 0),countStyle:.file))" + (result["trashed"] as? Bool == true ? "\n在废纸篓中 · \(result["deleted_at"] as? String ?? "")" : "")
            message.font = .systemFont(ofSize:15,weight:.semibold)
            records = [result["current_html"],result["entry_html"],result["folder"]].compactMap { $0 as? JSON } + (result["sources"] as? [JSON] ?? [])
            for (i,record) in records.enumerated() {
                let label = NSTextField(labelWithString:(record["label"] as? String ?? "位置")+" · "+(record["exists"] as? Bool == true ? "存在" : "已移动或删除")); label.font = .systemFont(ofSize:13,weight:.medium); label.textColor = record["exists"] as? Bool == true ? .labelColor : .systemOrange
                let path = NSTextField(wrappingLabelWithString:record["path"] as? String ?? ""); path.isSelectable = true; path.font = .monospacedSystemFont(ofSize:12,weight:.regular)
                var detail = "文件名："+(record["filename"] as? String ?? "")
                if let bytes = record["bytes"] as? Int { detail += " · "+ByteCountFormatter.string(fromByteCount:Int64(bytes),countStyle:.file) }
                if let seconds = record["modified_at"] as? Double { detail += " · "+Date(timeIntervalSince1970:seconds).formatted(date:.abbreviated,time:.shortened) }
                let note = NSTextField(wrappingLabelWithString:detail); note.font = .systemFont(ofSize:11); note.textColor = .secondaryLabelColor
                let copy = NSButton(title:"复制路径",target:self,action:#selector(copyRecord(_:))), link = NSButton(title:"复制本地链接",target:self,action:#selector(copyURL(_:))), reveal = NSButton(title:"在 Finder 中显示",target:self,action:#selector(revealRecord(_:)))
                for button in [copy,link,reveal] { button.tag = i; button.bezelStyle = .rounded; button.controlSize = .small }; reveal.isEnabled = record["exists"] as? Bool == true
                let actions = NSStackView(views:[copy,link,reveal]); actions.spacing = 8
                for view in [label,path,note,actions] { stack.addArrangedSubview(view) }; path.widthAnchor.constraint(equalTo:stack.widthAnchor).isActive = true; note.widthAnchor.constraint(equalTo:stack.widthAnchor).isActive = true
                let line = NSBox(); line.boxType = .separator; stack.addArrangedSubview(line); line.widthAnchor.constraint(equalTo:stack.widthAnchor).isActive = true
            }
            if (result["sources"] as? [JSON] ?? []).isEmpty { stack.addArrangedSubview(NSTextField(labelWithString:"由 Lumen 生成，未登记原始来源。")) }
            let repair = NSButton(title:"重新关联来源…",target:self,action:#selector(repairSource)); repair.bezelStyle = .rounded; stack.addArrangedSubview(repair)
            let note = NSTextField(wrappingLabelWithString:"文档标题与磁盘文件名分别显示。本地路径和 file 链接只在有对应文件的电脑上可访问；跨设备请导出正文或完整包。"); note.font = .systemFont(ofSize:12); note.textColor = .secondaryLabelColor; stack.addArrangedSubview(note); note.widthAnchor.constraint(equalTo:stack.widthAnchor).isActive = true
        } catch { message.stringValue = error.localizedDescription }
    }
    @objc func copyRecord(_ sender:NSButton) { guard records.indices.contains(sender.tag), let path = records[sender.tag]["path"] as? String else { return }; library?.copyUserText(path); message.toolTip = "已复制路径" }
    @objc func copyURL(_ sender:NSButton) { guard records.indices.contains(sender.tag), let path = records[sender.tag]["file_url"] as? String else { return }; library?.copyUserText(path); message.toolTip = "已复制本地链接" }
    @objc func revealRecord(_ sender:NSButton) { guard records.indices.contains(sender.tag), let path = records[sender.tag]["path"] as? String, NSApp.activationPolicy() != .prohibited else { return }; NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath:path)]) }
    @objc func repairSource() { let item = NSMenuItem(); item.representedObject = ["document_ids":[documentID]]; library?.relinkSource(item) }
}

@MainActor final class AIReadingController: NSWindowController, NSWindowDelegate {
    weak var library: LibraryWindowController?
    let documentID: String
    var pageID: String?, version: Int?
    let scope = NSPopUpButton(), mode = NSPopUpButton(), question = NSTextField(string:"请说明主要结论，并注明依据所在章节。")
    let text = NSTextView(), status = NSTextField(wrappingLabelWithString:""), coverage = NSTextField(wrappingLabelWithString:"")
    let copyButton = NSButton(), askButton = NSButton(), cancelButton = NSButton(), saveButton = NSButton(), appendButton = NSButton()
    var prepared: JSON?, answer: JSON?, taskID: String?, timer: Timer?, busy = false
    init(library:LibraryWindowController,documentID:String,pageID:String?,version:Int?,scope selectedScope:String) {
        self.library = library; self.documentID = documentID; self.pageID = pageID; self.version = version
        let window = NSWindow(contentRect:NSRect(x:0,y:0,width:940,height:720),styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:true)
        window.title = "交给 AI 阅读"; window.minSize = NSSize(width:720,height:540); window.isReleasedWhenClosed = false
        super.init(window:window); window.delegate = self
        scope.addItems(withTitles:["当前页","整份文档","选中文字"]); scope.selectItem(at:["page","document","selection"].firstIndex(of:selectedScope) ?? 0)
        mode.addItems(withTitles:["本机 Agent（路径与读取命令）","复制正文（无法访问本机的 AI）"]); mode.selectItem(at:LumenPaths.defaults.integer(forKey:"ai.copy_mode") == 1 ? 1 : 0)
        for control in [scope,mode] { control.target = self; control.action = #selector(optionsChanged) }
        let notes = NSButton(checkboxWithTitle:"附个人笔记",target:self,action:#selector(optionsChanged)), comments = NSButton(checkboxWithTitle:"附批注",target:self,action:#selector(optionsChanged)); notes.identifier = .init("notes"); comments.identifier = .init("feedback")
        let options = NSStackView(views:[scope,mode,notes,comments]); options.spacing = 10; options.identifier = .init("options")
        let rendered = NSButton(checkboxWithTitle:"附当前页渲染视图（脚本生成的可见内容）",target:self,action:#selector(optionsChanged)); rendered.identifier = .init("rendered")
        question.placeholderString = "你想让 AI 做什么？"; question.font = .systemFont(ofSize:14)
        text.isEditable = false; text.isRichText = false; text.font = .monospacedSystemFont(ofSize:13,weight:.regular); text.textContainerInset = NSSize(width:16,height:16); text.isVerticallyResizable = true; text.textContainer?.widthTracksTextView = true; text.autoresizingMask = [.width]
        let scroll = NSScrollView(); scroll.hasVerticalScroller = true; scroll.documentView = text; scroll.borderType = .bezelBorder
        for (button,title,action) in [(copyButton,"复制给 AI",#selector(copyMaterial)),(askButton,"让 AI 回答",#selector(ask)),(cancelButton,"取消任务",#selector(cancel)),(saveButton,"答案另存为文档…",#selector(saveAnswer)),(appendButton,"答案追加到原文…",#selector(appendAnswer))] { button.title = title; button.target = self; button.action = action; button.bezelStyle = .rounded }
        let attachment = NSButton(title:"导出阅读附件…",target:self,action:#selector(exportAttachment)); attachment.bezelStyle = .rounded
        let actions = NSStackView(views:[copyButton,attachment,askButton,cancelButton]); actions.spacing = 8
        let results = NSStackView(views:[saveButton,appendButton]); results.spacing = 8
        let root = NSStackView(views:[options,rendered,question,coverage,scroll,actions,results,status]); root.orientation = .vertical; root.alignment = .leading; root.spacing = 12; root.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(root)
        NSLayoutConstraint.activate([root.leadingAnchor.constraint(equalTo:window.contentView!.leadingAnchor,constant:20),root.trailingAnchor.constraint(equalTo:window.contentView!.trailingAnchor,constant:-20),root.topAnchor.constraint(equalTo:window.contentView!.topAnchor,constant:20),root.bottomAnchor.constraint(equalTo:window.contentView!.bottomAnchor,constant:-20),scroll.heightAnchor.constraint(greaterThanOrEqualToConstant:260)]+[question,coverage,scroll,status].map { $0.widthAnchor.constraint(equalTo:root.widthAnchor) })
        coverage.font = .systemFont(ofSize:12); coverage.textColor = .secondaryLabelColor; status.font = .systemFont(ofSize:12); status.textColor = .secondaryLabelColor
        saveButton.isEnabled = false; appendButton.isEnabled = false; cancelButton.isEnabled = false; window.center()
    }
    required init?(coder:NSCoder) { fatalError() }
    func windowWillClose(_ notification:Notification) { timer?.invalidate(); timer = nil }
    func readingParams() async throws -> JSON {
        guard let library else { throw EngineFailure("library_missing","知识库窗口已关闭") }
        guard try await library.prepareSavedAction(documentIDs:[documentID],purpose:"交给 AI 阅读") else { throw EngineFailure("cancelled","已取消，当前编辑保留") }
        if library.selectedDocument?.id == documentID, !library.viewingHistorical { version = library.selectedDocument?.version }
        var params: JSON = ["document_id":documentID,"scope":["page","document","selection"][scope.indexOfSelectedItem]]
        if let version { params["version"] = version }; if let pageID { params["page_id"] = pageID }
        for checkbox in (window?.contentView?.subviews.first as? NSStackView)?.arrangedSubviews.first.flatMap({ $0 as? NSStackView })?.arrangedSubviews.compactMap({ $0 as? NSButton }) ?? [] { params[checkbox.identifier?.rawValue == "notes" ? "include_notes" : "include_feedback"] = checkbox.state == .on }
        if let rendered = (window?.contentView?.subviews.first as? NSStackView)?.arrangedSubviews.first(where:{ $0.identifier?.rawValue == "rendered" }) as? NSButton, rendered.state == .on {
            guard scope.indexOfSelectedItem == 0, library.selectedDocument?.id == documentID, pageID == nil || library.selectedPage?.id == pageID else { throw EngineFailure("rendered_scope","渲染视图只用于阅读器正在显示的当前页") }
            let expected = ["document_id":documentID,"page_id":library.selectedPage?.id ?? "","version":version ?? 0] as JSON
            let markup = try await library.reader.callAsyncJavaScript("const p=JSON.parse(document.getElementById('html-feedback-init').textContent); if(p.document_id!==expected.document_id || p.page_id!==expected.page_id || p.version!==expected.version || window.LumenReader.hasUnsavedChanges)throw new Error('页面或版本已变化，请先保存并重新打开');return document.documentElement.outerHTML",arguments:["expected":expected],in:nil,contentWorld:.page) as? String
            guard let markup else { throw EngineFailure("rendered_missing","渲染视图未返回") }; params["rendered_html"] = markup
        }
        if scope.indexOfSelectedItem == 2 {
            guard library.selectedDocument?.id == documentID, pageID == nil || library.selectedPage?.id == pageID else { throw EngineFailure("selection_missing","请先在这份文档的目标页面选择文字") }
            let selection = try await library.reader.evaluateJavaScript("window.getSelection().toString()") as? String ?? ""
            guard !selection.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty else { throw EngineFailure("selection_missing","请先在阅读页选择文字") }; params["selection"] = selection
        }
        return params
    }
    @objc func optionsChanged() { guard !busy else { return }; prepared = nil; LumenPaths.defaults.set(mode.indexOfSelectedItem,forKey:"ai.copy_mode"); Task { await prepare() } }
    func prepare() async {
        guard !busy, let library else { return }; busy = true; copyButton.isEnabled = false; status.stringValue = "正在准备保存版阅读材料…"
        defer { busy = false; copyButton.isEnabled = prepared != nil }
        do {
            var params = try await readingParams(); params["mode"] = mode.indexOfSelectedItem == 0 ? "local" : "text"; params["request"] = question.stringValue
            let result = try await library.engine.call("prepare-ai",params,timeout:75); prepared = result
            text.string = result["copy_text"] as? String ?? result["text"] as? String ?? ""
            let value = result["coverage"] as? JSON ?? [:], omissions = value["omissions"] as? [JSON] ?? []
            coverage.stringValue = "\(result["title"] as? String ?? "文档") · v\(result["version"] as? Int ?? 0)（已保存） · \(scope.titleOfSelectedItem ?? "") · \(value["extracted_pages"] as? Int ?? 0)/\(value["requested_pages"] as? Int ?? 0) 页 · \(value["characters"] as? Int ?? 0) 字符" + (omissions.isEmpty ? "" : " · \(omissions.count) 项图像/交互内容未提取")
            status.stringValue = mode.indexOfSelectedItem == 0 ? "本机 Agent 需要相关文件访问权限。点击复制只准备材料，不会发送给任何 AI。" : "复制正文后粘贴到目标 AI；长文可导出附件。个人笔记与批注仅在勾选后附带。"
        } catch { prepared = nil; status.stringValue = error.localizedDescription }
    }
    @objc func copyMaterial() { Task { await prepare(); if let value = prepared?["copy_text"] as? String { library?.copyUserText(value); status.stringValue = "已复制 · \(scope.titleOfSelectedItem ?? "") · v\(prepared?["version"] as? Int ?? 0)" } } }
    @objc func exportAttachment() {
        guard let window, let library else { return }
        let panel = NSSavePanel(); panel.canCreateDirectories = true; panel.nameFieldStringValue = "AI阅读材料.zip"
        panel.beginSheetModal(for:window) { [weak self] response in
            guard response == .OK, let output = panel.url, let self else { return }
            Task { do { var params = try await self.readingParams(); params["mode"] = "attachment"; params["output"] = output.path; params["request"] = self.question.stringValue; _ = try await library.engine.call("prepare-ai",params,timeout:75); self.status.stringValue = "已导出阅读附件 · "+output.path } catch { self.status.stringValue = error.localizedDescription } }
        }
    }
    @objc func ask() {
        guard !busy, let library else { return }; busy = true; askButton.isEnabled = false
        Task { [self] in
            defer { busy = false }
            do {
                var params = try await readingParams(); params["request"] = question.stringValue
                let result = try await library.engine.call("ask",params,timeout:75)
                guard let task = result["task"] as? JSON, let id = task["id"] as? String else { throw EngineFailure("task_missing","没有返回 AI 任务编号") }
                taskID = id; prepared = nil; answer = nil; text.string = "AI 正在阅读所选保存版材料。关闭窗口后任务仍可接续。"; cancelButton.isEnabled = true; saveButton.isEnabled = false; appendButton.isEnabled = false
                scope.isEnabled = false; mode.isEnabled = false
                status.stringValue = "任务 \(id) · \(scope.titleOfSelectedItem ?? "") · v\(version ?? 0)"
                timer?.invalidate(); timer = Timer.scheduledTimer(withTimeInterval:1.5,repeats:true) { [weak self] _ in Task { await self?.poll() } }; await poll()
            } catch { askButton.isEnabled = true; status.stringValue = error.localizedDescription }
        }
    }
    func poll() async {
        guard let taskID, let library else { return }
        do { let result = try await library.engine.call("task",["task_id":taskID]); if let task = result["task"] as? JSON { accept(task:task) } } catch { status.stringValue = error.localizedDescription }
    }
    func accept(task:JSON) {
        taskID = task["id"] as? String; let state = task["status"] as? String ?? "unknown"
        if ["succeeded","failed","cancelled","interrupted"].contains(state) {
            timer?.invalidate(); timer = nil; cancelButton.isEnabled = false; askButton.isEnabled = true; scope.isEnabled = true; mode.isEnabled = true
            if state == "succeeded", let result = task["result"] as? JSON {
                answer = result; text.string = result["answer"] as? String ?? ""; saveButton.isEnabled = true; appendButton.isEnabled = true
                let params = task["params"] as? JSON ?? [:], value = result["coverage"] as? JSON ?? [:]
                coverage.stringValue = "答案预览 · 来源 v\(params["source_version"] as? Int ?? 0) · \(params["read_scope"] as? String ?? "") · \(value["extracted_pages"] as? Int ?? 0) 页"
                status.stringValue = "AI 回答已完成，原文未改动。可另存新文档，或确认后追加到结构化原文。"
            } else { status.stringValue = (task["error"] as? JSON)?["message"] as? String ?? "任务\(state)，可在任务窗口接续。" }
        } else { status.stringValue = "任务 \(taskID ?? "") · \(task["step"] as? String ?? state)" }
    }
    @objc func cancel() { guard let taskID, let library else { return }; Task { do { _ = try await library.engine.call("cancel",["task_id":taskID]); await poll() } catch { status.stringValue = error.localizedDescription } } }
    @objc func saveAnswer() {
        guard let content = answer?["content"] as? JSON, let library else { return }
        Task {
            do {
                guard let (title,_) = await library.showPrompt(title:"答案另存为文档",initial:content["title"] as? String ?? "AI 阅读结果") else { return }
                let result = try await saveAnswerContent(title:title)
                saveButton.isEnabled = false; status.stringValue = "答案已另存，原文保留"; await library.refresh()
                if let doc = result["document"] as? JSON, let id = doc["id"] as? String { await library.open(documentID:id) }
            } catch { status.stringValue = error.localizedDescription }
        }
    }
    func saveAnswerContent(title:String) async throws -> JSON {
        guard var content=answer?["content"] as? JSON, let taskID, let library else { throw EngineFailure("answer_missing","答案尚未完成") }
        content["title"]=title
        let original=try await library.engine.call("get",["document_id":documentID])
        return try await library.engine.call("build",["content":content,"kind":"report","project":(original["document"] as? JSON)?["project"] as? String ?? "","sources":[["document_id":documentID,"version":answer?["source_version"] as? Int ?? version ?? 0,"role":"ai-reading-source"]],"idempotency_key":"ask-save:"+taskID])
    }
    func answerAppendPlan() async throws -> JSON {
        guard let result=answer, let draft=result["content"] as? JSON, let library else { throw EngineFailure("answer_missing","答案尚未完成") }
        let original=try await library.engine.call("get",["document_id":documentID])
        guard var content=original["content"] as? JSON, let oldPages=content["pages"] as? [JSON] else { throw EngineFailure("unstructured_source","普通 HTML 的答案请另存文档；正文可继续用阅读器编辑。") }
        let expected=result["source_version"] as? Int ?? 0
        guard (original["document"] as? JSON)?["version"] as? Int == expected else { throw EngineFailure("conflict","原文已有新版本；请重新阅读后决定，当前答案仍保留。") }
        let pages=draft["pages"] as? [JSON] ?? [], prefix="ai-"+String((taskID ?? UUID().uuidString).suffix(12))+"-"
        let ids=Dictionary(uniqueKeysWithValues:pages.compactMap { p -> (String,String)? in guard let id=p["id"] as? String else { return nil }; return (id,prefix+id) })
        let newPages=pages.map { page -> JSON in
            var p=page; p["id"]=ids[page["id"] as? String ?? ""]
            if let parent=page["parent"] as? String { p["parent"]=ids[parent] }
            if let blocks=page["blocks"] as? [JSON] { p["blocks"]=blocks.map { block -> JSON in var b=block; if let items=b["items"] as? [JSON], b["type"] as? String == "links" { b["items"]=items.map { item -> JSON in var i=item; if let id=item["page"] as? String { i["page"]=ids[id] ?? id }; return i } }; return b } }
            return p
        }
        content["pages"]=oldPages+newPages
        return ["document_id":documentID,"expected_version":expected,"content":content,"kind":(original["document"] as? JSON)?["kind"] as? String ?? "portal"]
    }
    func commitAnswerAppend(_ plan:JSON) async throws -> JSON {
        guard let library else { throw EngineFailure("document_missing","原文窗口已关闭") }
        return try await library.engine.call("build",plan)
    }
    @objc func appendAnswer() {
        guard answer != nil, let library, let window else { return }
        Task {
            do {
                guard try await library.prepareSavedAction(documentIDs:[documentID],purpose:"追加 AI 答案") else { return }
                let plan=try await answerAppendPlan(), pages=(plan["content"] as? JSON)?["pages"] as? [JSON] ?? [], added=(answer?["content"] as? JSON)?["pages"] as? [JSON] ?? []
                let expected=plan["expected_version"] as? Int ?? 0
                let alert = NSAlert(); alert.messageText = "把答案追加到原文的新版本？"; alert.informativeText = "保留原文 \(pages.count-added.count) 页，追加 \(added.count) 页：\n"+added.compactMap { $0["title"] as? String }.joined(separator:"\n")+"\n\n以 v\(expected) 为基线；已有新版本时停止提交。"
                alert.addButton(withTitle:"追加并保存新版本"); alert.addButton(withTitle:"取消")
                guard await alert.beginSheetModal(for:window) == .alertFirstButtonReturn else { return }
                _ = try await commitAnswerAppend(plan)
                appendButton.isEnabled = false; status.stringValue = "答案已追加为新版本，原文与历史保留"; await library.open(documentID:documentID); await library.refresh()
            } catch { status.stringValue = error.localizedDescription }
        }
    }
}

extension LibraryWindowController: NSMenuItemValidation, NSFilePromiseProviderDelegate {
    var selectedDocumentIDs: [String] {
        let ids = documentsTable.selectedRowIndexes.compactMap { row in
            matches.isEmpty ? (documents.indices.contains(row) ? documents[row].id : nil) : (matches.indices.contains(row) ? matches[row].documentID : nil)
        }
        return ids.isEmpty ? selectedDocument.map { [$0.id] } ?? [] : Array(NSOrderedSet(array:ids)) as? [String] ?? ids
    }
    func menuTargets(_ sender: Any?) -> JSON {
        if let item = sender as? NSMenuItem, let target = item.representedObject as? JSON { return target }
        var value: JSON = ["document_ids":selectedDocument.map { [$0.id] } ?? []]
        if let page = selectedPage { value["page_id"] = page.id }
        if let document = selectedDocument { value["version"] = document.version }
        return value
    }
    func documentMenu(row: Int? = nil) -> NSMenu {
        let menu = NSMenu(); menu.autoenablesItems = false
        let ids = row == nil ? selectedDocument.map { [$0.id] } ?? [] : selectedDocumentIDs
        var target: JSON = ["document_ids":ids]
        var first = selectedDocument
        if let row {
            if !matches.isEmpty, matches.indices.contains(row) {
                target["page_id"] = matches[row].pageID
                first = documents.first { $0.id == matches[row].documentID }
            } else if documents.indices.contains(row) { first = documents[row] }
        } else if let page = selectedPage { target["page_id"] = page.id }
        if let first { target["version"] = first.version }
        func item(_ title: String, _ action: Selector, value: JSON = [:], enabled: Bool = true) {
            let entry = NSMenuItem(title:title,action:action,keyEquivalent:""); entry.target = self; entry.representedObject = target.merging(value) { _,b in b }; entry.isEnabled = enabled && !ids.isEmpty; menu.addItem(entry)
        }
        if first?.trashed == true {
            item(T("mac.label.untrash","恢复文档"),#selector(restoreTrashedDocuments(_:)))
            item(T("mac.label.information","文档信息"),#selector(showDocumentInformationFromMenu(_:)),enabled:ids.count == 1)
            menu.addItem(.separator()); item(T("mac.label.purge","彻底删除…"),#selector(purgeDocuments(_:)))
            return menu
        }
        item("打开",#selector(openDocumentFromMenu(_:)),enabled:ids.count == 1)
        item(T("mac.label.open_browser","在浏览器中打开"),#selector(openInBrowser(_:)),enabled:ids.count == 1)
        item(T("mac.label.rename","重命名…"),#selector(renameDocument(_:)),enabled:ids.count == 1)
        item(T("mac.label.duplicate","复制文档…"),#selector(duplicateDocument(_:)),enabled:ids.count == 1)
        let ai = NSMenu(title:"复制给 AI")
        for (title,scope) in [("当前页","page"),("整份文档","document"),("选中文字","selection")] {
            let entry = NSMenuItem(title:title,action:#selector(showAIFromMenu(_:)),keyEquivalent:""); entry.target = self
            entry.representedObject = target.merging(["scope":scope]) { _,b in b }; entry.isEnabled = ids.count == 1; ai.addItem(entry)
        }
        let aiItem = NSMenuItem(title:T("mac.label.copy_ai","复制给 AI"),action:nil,keyEquivalent:""); aiItem.submenu = ai; aiItem.isEnabled = ids.count == 1; menu.addItem(aiItem)
        for (title,action) in [("复制路径",#selector(copyPath(_:))),("在 Finder 中显示",#selector(revealPath(_:)))] {
            let sub = NSMenu(title:title)
            for (label,kind) in [("当前 HTML","current_html"),("文档入口","entry_html"),("文档文件夹","folder"),("原始来源","source")] {
                let entry = NSMenuItem(title:label,action:action,keyEquivalent:""); entry.target = self; entry.isEnabled = ids.count == 1
                entry.representedObject = target.merging(["path_kind":kind]) { _,b in b }; sub.addItem(entry)
            }
            let parent = NSMenuItem(title:title,action:nil,keyEquivalent:""); parent.submenu = sub; parent.isEnabled = ids.count == 1; menu.addItem(parent)
        }
        item(T("mac.label.information","文档信息"),#selector(showDocumentInformationFromMenu(_:)),enabled:ids.count == 1)
        item("导出…",#selector(exportFromMenu(_:)))
        item("导出并编辑副本…",#selector(editExternalCopy(_:)),enabled:ids.count == 1)
        item("重新导入编辑副本…",#selector(importEditedCopy(_:)),enabled:ids.count == 1)
        item(T("mac.label.rename_file","更改 HTML 文件名…"),#selector(renameHTMLFile(_:)),enabled:ids.count == 1)
        item(T("mac.label.relink_source","重新关联来源…"),#selector(relinkSource(_:)),enabled:ids.count == 1)
        menu.addItem(.separator())
        item(first?.favorite == true ? "取消收藏" : "收藏",#selector(batchMetadata(_:)),value:["values":["favorite":first?.favorite != true]])
        item(first?.archived == true ? "取消归档" : "归档",#selector(batchMetadata(_:)),value:["values":["archived":first?.archived != true]])
        item("设置项目与标签…",#selector(setClassification(_:)))
        item(T("mac.label.trash","移到废纸篓"),#selector(trashDocuments(_:)))
        if !lastTrashedIDs.isEmpty { item("撤销上次移到废纸篓",#selector(undoTrash(_:)),enabled:true) }
        return menu
    }
    @objc func showMoreMenu(_ sender: NSButton) { documentMenu().popUp(positioning:nil,at:NSPoint(x:0,y:sender.bounds.maxY),in:sender) }
    @objc func sortChanged() { LumenPaths.defaults.set(sortPopup.indexOfSelectedItem,forKey:"library.sort"); Task { await refresh() } }
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        if item.action == #selector(trashDocuments(_:)), window?.firstResponder is NSTextView { return false }
        return selectedDocument != nil
    }
    func currentDocument(_ id: String) async throws -> KnowledgeDocument {
        let result = try await engine.call("get",["document_id":id])
        guard let json = result["document"] as? JSON, let doc = KnowledgeDocument(json) else { throw EngineFailure("document_missing","文档信息未返回") }
        return doc
    }
    func showPrompt(title: String, message: String = "", initial: String, checkboxes: [String] = []) async -> (String,[Bool])? {
        guard NSApp.activationPolicy() != .prohibited, let window else { return nil }
        let alert = NSAlert(); alert.messageText = title; alert.informativeText = message
        let field = NSTextField(string:initial); field.frame = NSRect(x:0,y:0,width:420,height:26)
        let checks = checkboxes.map { NSButton(checkboxWithTitle:$0,target:nil,action:nil) }
        let view = NSStackView(views:[field]+checks); view.orientation = .vertical; view.alignment = .leading; view.spacing = 10; view.frame = NSRect(x:0,y:0,width:420,height:CGFloat(32+checks.count*30))
        alert.accessoryView = view; alert.addButton(withTitle:"保存"); alert.addButton(withTitle:"取消")
        window.makeFirstResponder(field)
        guard await alert.beginSheetModal(for:window) == .alertFirstButtonReturn else { return nil }
        return (field.stringValue,checks.map { $0.state == .on })
    }
    @objc func renameDocument(_ sender: Any?) {
        let target = menuTargets(sender); guard let id = (target["document_ids"] as? [String])?.first else { return }
        Task {
            do {
                guard try await prepareSavedAction(documentIDs:[id],purpose:"重命名") else { return }
                let doc = try await currentDocument(id)
                guard let (title,checks) = await showPrompt(title:"重命名",message:"文档显示名和默认导出名会更新；磁盘 HTML 文件名可在更多菜单单独修改。",initial:doc.title,checkboxes:["同时更新文档内容标题（结构化文档，新版本）"]) else { return }
                _ = try await engine.call("rename",["document_id":id,"title":title,"expected_version":doc.version,"expected_metadata_revision":doc.metadataRevision,"sync_content_title":checks.first == true])
                await refresh(); if selectedDocument?.id == id { await open(documentID:id,pageID:selectedPage?.id) }; status.stringValue = "已重命名"
            } catch { showError(error) }
        }
    }
    @objc func renameHTMLFile(_ sender: Any?) {
        let target = menuTargets(sender); guard let id = (target["document_ids"] as? [String])?.first else { return }
        Task {
            do {
                guard try await prepareSavedAction(documentIDs:[id],purpose:"更改 HTML 文件名") else { return }
                let doc = try await currentDocument(id)
                var params: JSON = ["document_id":id]; if let page = target["page_id"] { params["page_id"] = page }
                let paths = try await engine.call("paths",params)
                guard let file = paths["current_html"] as? JSON, let filename = file["filename"] as? String,
                      let (_,pages) = try? await documentPages(id), let pageID = target["page_id"] as? String ?? pages.first?.id,
                      let (name,_) = await showPrompt(title:"更改 HTML 文件名",message:"创建新版本并更新托管包内链接；原始来源文件保留。",initial:filename) else { return }
                _ = try await engine.call("rename-file",["document_id":id,"page_id":pageID,"filename":name,"expected_version":doc.version,"expected_metadata_revision":doc.metadataRevision])
                await refresh(); if selectedDocument?.id == id { await open(documentID:id,pageID:pageID) }; status.stringValue = "HTML 文件名已更新，旧版本保留"
            } catch { showError(error) }
        }
    }
    func documentPages(_ id: String) async throws -> (JSON,[KnowledgePage]) {
        let result = try await engine.call("get",["document_id":id]); return (result,(result["pages"] as? [JSON] ?? []).compactMap(KnowledgePage.init))
    }
    @objc func duplicateDocument(_ sender: Any?) {
        let target = menuTargets(sender); guard let id = (target["document_ids"] as? [String])?.first else { return }
        Task {
            do {
                guard try await prepareSavedAction(documentIDs:[id],purpose:"复制文档") else { return }
                let doc = try await currentDocument(id)
                guard let (title,checks) = await showPrompt(title:"复制文档",initial:doc.title+" 副本",checkboxes:["带个人笔记","带批注","复制全部历史版本"]) else { return }
                let result = try await engine.call("duplicate",["document_id":id,"title":title,"include_notes":checks[0],"include_feedback":checks[1],"include_history":checks[2]])
                await refresh(); if let copy = result["document"] as? JSON, let newID = copy["id"] as? String { await open(documentID:newID) }; status.stringValue = "独立副本已创建"
            } catch { showError(error) }
        }
    }
    @objc func relinkSource(_ sender: Any?) {
        let target = menuTargets(sender); guard let id = (target["document_ids"] as? [String])?.first, let window else { return }
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = true; panel.message = "选择移动后的原始来源，保留以前的来源记录。"
        panel.beginSheetModal(for:window) { [weak self] response in
            guard response == .OK, let source = panel.url, let self else { return }
            Task { do { let doc = try await self.currentDocument(id); _ = try await self.engine.call("relink-source",["document_id":id,"source":source.path,"expected_version":doc.version,"expected_metadata_revision":doc.metadataRevision]); if self.selectedDocument?.id == id { await self.open(documentID:id) }; self.status.stringValue = "来源已重新关联" } catch { self.showError(error) } }
        }
    }
    @objc func trashDocuments(_ sender: Any?) { runTrash("trash",sender:sender) }
    @objc func restoreTrashedDocuments(_ sender: Any?) { runTrash("untrash",sender:sender) }
    @objc func undoTrash(_ sender: Any?) {
        guard !lastTrashedIDs.isEmpty else { return }; let ids = lastTrashedIDs
        Task { do { let result = try await engine.call("batch",["document_ids":ids,"action":"untrash"]); lastTrashedIDs = []; await refresh(); reportBatch(result,verb:"恢复") } catch { showError(error) } }
    }
    func reportBatch(_ result: JSON, verb: String) {
        let success = result["succeeded"] as? Int ?? 0, failed = result["failed"] as? Int ?? 0
        status.stringValue = "\(verb)：\(success) 份成功" + (failed > 0 ? "，\(failed) 份失败" : "")
        status.toolTip = (result["results"] as? [JSON] ?? []).filter { $0["ok"] as? Bool == false }.map { ($0["document_id"] as? String ?? "")+"："+(($0["error"] as? JSON)?["message"] as? String ?? "未完成") }.joined(separator:"\n")
        status.textColor = failed > 0 ? .systemOrange : .secondaryLabelColor
        if failed > 0, NSApp.activationPolicy() != .prohibited, let window {
            let failures=(result["results"] as? [JSON] ?? []).filter { $0["ok"] as? Bool == false }
            let busy=failures.contains { ($0["error"] as? JSON)?["code"] as? String == "document_busy" }
            let alert=NSAlert(); alert.messageText="\(verb)：\(failed) 份未完成"; alert.informativeText=status.toolTip ?? "请重新读取文档后再试。"
            if busy { alert.addButton(withTitle:"查看并取消关联任务") }; alert.addButton(withTitle:"知道了")
            alert.beginSheetModal(for:window) { [weak self] response in if busy && response == .alertFirstButtonReturn { Task { @MainActor in self?.showTasks() } } }
        }
    }
    func runTrash(_ action: String, sender: Any?) {
        let target = menuTargets(sender); let ids = target["document_ids"] as? [String] ?? []; guard !ids.isEmpty else { return }
        Task {
            do {
                guard try await prepareSavedAction(documentIDs:ids,purpose:action == "trash" ? "移到废纸篓" : "恢复文档") else { return }
                if action == "trash", ids.count > 1 || !LumenPaths.defaults.bool(forKey:"trash.informed") {
                    guard NSApp.activationPolicy() != .prohibited, let window else { throw EngineFailure("confirmation_required","请在正常窗口确认移到废纸篓") }
                    let alert = NSAlert(); alert.messageText = "将 \(ids.count) 份文档移到废纸篓？"; alert.informativeText = "Lumen 托管内容、版本、笔记与批注可恢复；原项目资料仍保留。"
                    alert.addButton(withTitle:"移到废纸篓"); alert.addButton(withTitle:"取消")
                    guard await alert.beginSheetModal(for:window) == .alertFirstButtonReturn else { return }; LumenPaths.defaults.set(true,forKey:"trash.informed")
                }
                let result = try await engine.call("batch",["document_ids":ids,"action":action])
                if action == "trash" { lastTrashedIDs = (result["results"] as? [JSON] ?? []).filter { $0["ok"] as? Bool == true }.compactMap { $0["document_id"] as? String } }
                if let selected = selectedDocument, ids.contains(selected.id) { await open(documentID:selected.id,pageID:selectedPage?.id) }
                await refresh(); reportBatch(result,verb:action == "trash" ? "移到废纸篓" : "恢复")
            } catch { showError(error); if (error as? EngineFailure)?.code == "document_busy" { showTasks() } }
        }
    }
    @objc func purgeDocuments(_ sender: Any?) {
        let target = menuTargets(sender); let ids = target["document_ids"] as? [String] ?? []
        guard !ids.isEmpty, let window, NSApp.activationPolicy() != .prohibited else { return }
        Task {
            do {
                var descriptions: [String] = []
                for id in ids { let info = try await engine.call("paths",["document_id":id]); descriptions.append("\(info["title"] as? String ?? id) · \(info["page_count"] as? Int ?? 0) 页 · \(ByteCountFormatter.string(fromByteCount:Int64(info["storage_bytes"] as? Int ?? 0),countStyle:.file))") }
                let alert = NSAlert(); alert.alertStyle = .warning; alert.messageText = "彻底删除 \(ids.count) 份文档？"
                alert.informativeText = descriptions.joined(separator:"\n")+"\n\n此操作不可恢复，将清理 Lumen 托管内容和历史版本；原始来源仍保留。"
                alert.addButton(withTitle:"彻底删除"); alert.addButton(withTitle:"取消")
                guard await alert.beginSheetModal(for:window) == .alertFirstButtonReturn else { return }
                var failed: [String] = []
                for id in ids { do { let doc = try await currentDocument(id); _ = try await engine.call("purge",["document_id":id,"confirmed":true,"expected_version":doc.version,"expected_metadata_revision":doc.metadataRevision]) } catch { failed.append(id+"："+error.localizedDescription) } }
                if let doc = selectedDocument, ids.contains(doc.id), !failed.contains(where: { $0.hasPrefix(doc.id) }) {
                    selectedDocument = nil; selectedPage = nil; pages = []; pagePopup.removeAllItems(); reader.loadHTMLString("<html><meta charset='utf-8'><p>文档已彻底删除，请从列表选择另一份文档。</p></html>",baseURL:nil); updateDocumentUI()
                }
                await refresh(); status.stringValue = "彻底删除：\(ids.count-failed.count) 份完成，\(failed.count) 份失败"; status.toolTip = failed.joined(separator:"\n")
            } catch { showError(error) }
        }
    }
    @objc func batchMetadata(_ sender: Any?) {
        let target = menuTargets(sender); guard let ids = target["document_ids"] as? [String], let values = target["values"] as? JSON else { return }
        Task { do { guard try await prepareSavedAction(documentIDs:ids,purpose:"更新文档分类") else { return }; let result = try await engine.call("batch",["document_ids":ids,"action":"metadata","values":values]); if let doc = selectedDocument, ids.contains(doc.id) { await open(documentID:doc.id,pageID:selectedPage?.id) }; await refresh(); reportBatch(result,verb:"更新") } catch { showError(error) } }
    }
    @objc func setClassification(_ sender: Any?) {
        let target = menuTargets(sender); guard let ids = target["document_ids"] as? [String], !ids.isEmpty else { return }
        Task {
            guard let (project,_) = await showPrompt(title:"设置所属项目",message:"应用于 \(ids.count) 份文档；留空清除项目。",initial:ids.count == 1 ? selectedDocument?.project ?? "" : "") else { return }
            guard let (tags,_) = await showPrompt(title:"设置主题标签",message:"逗号分隔；留空清除标签。",initial:ids.count == 1 ? selectedDocument?.tags.joined(separator:", ") ?? "" : "") else { return }
            let item = NSMenuItem(); item.representedObject = target.merging(["values":["project":project,"tags":tags.split(separator:",").map { $0.trimmingCharacters(in:.whitespacesAndNewlines) }.filter { !$0.isEmpty }]]) { _,b in b }; batchMetadata(item)
        }
    }
    func pathValue(_ target: JSON) async throws -> JSON {
        guard let id = (target["document_ids"] as? [String])?.first else { throw EngineFailure("document_missing","请选择文档") }
        var params: JSON = ["document_id":id]; for key in ["page_id","version"] { if let value = target[key] { params[key] = value } }
        let info = try await engine.call("paths",params), kind = target["path_kind"] as? String ?? "current_html"
        let path = kind == "source" ? (info["sources"] as? [JSON])?.first : info[kind] as? JSON
        guard let path else { throw EngineFailure("source_missing","这份文档没有登记原始来源") }; return path
    }
    @objc func copyPath(_ sender: Any?) {
        let target = menuTargets(sender); Task { do { let value = try await pathValue(target); guard let path = value["path"] as? String else { return }; copyUserText(path); status.stringValue = "已复制\(value["label"] as? String ?? "文件")路径" } catch { showError(error) } }
    }
    @objc func revealPath(_ sender: Any?) {
        let target = menuTargets(sender); Task { do { let value = try await pathValue(target); guard let path = value["path"] as? String else { return }; guard value["exists"] as? Bool == true else { throw EngineFailure("source_missing","文件已移动或删除；仍可复制记录的路径或重新关联来源。") }; if NSApp.activationPolicy() != .prohibited { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath:path)]) } } catch { showError(error) } }
    }
    @objc func openInBrowser(_ sender: Any?) {
        var target = menuTargets(sender); target["path_kind"] = "current_html"
        Task { do { let value = try await pathValue(target); if let path = value["path"] as? String, value["exists"] as? Bool == true, NSApp.activationPolicy() != .prohibited { NSWorkspace.shared.open(URL(fileURLWithPath:path)) } } catch { showError(error) } }
    }
    @objc func openDocumentFromMenu(_ sender:Any?) {
        let target=menuTargets(sender); guard let id=(target["document_ids"] as? [String])?.first else { return }
        Task { await open(documentID:id,pageID:target["page_id"] as? String,version:target["version"] as? Int) }
    }
    @objc func editExternalCopy(_ sender:Any?) {
        let target=menuTargets(sender); guard let id=(target["document_ids"] as? [String])?.first, let window else { return }
        Task { [self] in
            do {
                guard try await prepareSavedAction(documentIDs:[id],purpose:"导出编辑副本") else { return }
                let doc=try await currentDocument(id), panel=NSSavePanel(); panel.canCreateDirectories=true; panel.nameFieldStringValue=safeFilename(doc.title)+"-编辑副本"
                guard await panel.beginSheetModal(for:window) == .OK, let folder=panel.url else { return }
                let result=try await engine.call("export-folder",["document_id":id,"version":doc.version,"output":folder.path],timeout:75)
                LumenPaths.defaults.set(folder.path,forKey:"editing_copy."+id)
                var entry=result["entry_path"] as? String ?? ""
                if let pageID=target["page_id"] as? String {
                    let (_,pages)=try await documentPages(id)
                    if let page=pages.first(where:{ $0.id==pageID }) { entry=folder.appendingPathComponent("site").appendingPathComponent(page.relativePath).path }
                }
                status.stringValue="编辑副本已导出 · "+folder.path
                let application=NSOpenPanel(); application.canChooseFiles=true; application.canChooseDirectories=false; application.allowedContentTypes=[.applicationBundle]; application.directoryURL=URL(fileURLWithPath:"/Applications",isDirectory:true); application.message="选择用于编辑这份 HTML 副本的应用程序。改完后，在更多菜单选择重新导入编辑副本。"
                guard await application.beginSheetModal(for:window) == .OK, let editor=application.url else { return }
                if NSApp.activationPolicy() != .prohibited {
                    NSWorkspace.shared.open([URL(fileURLWithPath:entry)],withApplicationAt:editor,configuration:NSWorkspace.OpenConfiguration()) { [weak self] _,error in if let error { Task { @MainActor in self?.showError(error) } } }
                }
            } catch { showError(error) }
        }
    }
    @objc func importEditedCopy(_ sender:Any?) {
        let target=menuTargets(sender); guard let id=(target["document_ids"] as? [String])?.first, let window else { return }
        let panel=NSOpenPanel(); panel.canChooseFiles=true; panel.canChooseDirectories=true; panel.message="选择编辑副本目录以收齐所有资源，或选择修改后的 HTML。将更新此文档为新版本，旧版本保留。"
        if let path=LumenPaths.defaults.string(forKey:"editing_copy."+id) { panel.directoryURL=URL(fileURLWithPath:path,isDirectory:true) }
        panel.beginSheetModal(for:window) { [weak self] response in
            guard response == .OK, let input=panel.url, let self else { return }
            Task {
                do {
                    guard try await self.prepareSavedAction(documentIDs:[id],purpose:"导入编辑副本") else { return }
                    let doc=try await self.currentDocument(id)
                    let alert=NSAlert(); alert.messageText="将编辑副本更新到「\(doc.title)」？"; alert.informativeText="来源：\(input.path)\n以 v\(doc.version) 为基线建立新版本，保留原始来源、笔记、批注和全部历史。外部 HTML 修改会作为页面快照保存。"
                    alert.addButton(withTitle:"更新并保留旧版本"); alert.addButton(withTitle:"取消")
                    guard await alert.beginSheetModal(for:window) == .alertFirstButtonReturn else { return }
                    _ = try await self.engine.call("import",["path":input.path,"document_id":id,"on_duplicate":"update","expected_version":doc.version,"include_test":false],timeout:75)
                    await self.open(documentID:id,pageID:target["page_id"] as? String); await self.refresh(); self.status.stringValue="编辑副本已保存为新版本，原文历史保留"
                } catch { self.showError(error) }
            }
        }
    }
    func copyUserText(_ value: String) {
        let pasteboard: NSPasteboard
        if NSApp.activationPolicy() == .prohibited {
            guard let domain = ProcessInfo.processInfo.environment["LUMEN_DEFAULTS_DOMAIN"], let name = ProcessInfo.processInfo.environment["LUMEN_TEST_PASTEBOARD"], name.hasPrefix("lumen-test-"+domain) else { return }
            pasteboard = NSPasteboard(name:.init(name))
        } else { pasteboard = .general }
        pasteboard.clearContents(); pasteboard.setString(value,forType:.string)
    }
    @objc func showDocumentInformation() { showDocumentInformationFromMenu(nil) }
    @objc func showDocumentInformationFromMenu(_ sender: Any?) {
        let target = menuTargets(sender); guard let id = (target["document_ids"] as? [String])?.first else { return }
        let controller = DocumentInformationController(library:self,documentID:id,pageID:target["page_id"] as? String,version:target["version"] as? Int)
        informationWindow = controller; Task { await controller.load() }; if NSApp.activationPolicy() != .prohibited { controller.showWindow(nil) }
    }
    func copyCurrentMaterial() async throws -> JSON {
        guard let document = selectedDocument, let page = selectedPage else { throw EngineFailure("document_missing","请选择文档") }
        guard try await prepareSavedAction(documentIDs:[document.id],purpose:"复制给 AI") else { throw EngineFailure("cancelled","已取消，当前编辑保留") }
        let current = selectedDocument ?? document
        let result = try await engine.call("prepare-ai",["document_id":current.id,"page_id":page.id,"version":current.version,"scope":"page","mode":LumenPaths.defaults.integer(forKey:"ai.copy_mode") == 1 ? "text" : "local"],timeout:75)
        if let material = result["copy_text"] as? String { copyUserText(material) }
        status.stringValue = "已复制给 AI · 当前页 · v\(current.version)"; status.toolTip = "更多菜单可选择整份文档、选中文字、正文模式与内置 AI 提问。"
        return result
    }
    @objc func copyCurrentForAI() { Task { do { _ = try await copyCurrentMaterial() } catch { showError(error) } } }
    @objc func showAIFromMenu(_ sender: Any?) {
        let target = menuTargets(sender); guard let id = (target["document_ids"] as? [String])?.first else { return }
        let controller = AIReadingController(library:self,documentID:id,pageID:target["page_id"] as? String,version:target["version"] as? Int,scope:target["scope"] as? String ?? "page")
        aiWindow = controller; if NSApp.activationPolicy() != .prohibited { controller.showWindow(nil) }; Task { await controller.prepare() }
    }
    func showAIResult(task: JSON) {
        let params = task["params"] as? JSON ?? [:]
        guard let id = params["source_document_id"] as? String else { return }
        let controller = AIReadingController(library:self,documentID:id,pageID:params["source_page_id"] as? String,version:params["source_version"] as? Int,scope:params["read_scope"] as? String ?? "page")
        aiWindow = controller; controller.accept(task:task); if NSApp.activationPolicy() != .prohibited { controller.showWindow(nil) }
    }
    @objc func exportFromMenu(_ sender: Any?) { showExportOptions(target:menuTargets(sender)) }
    func showExportOptions(target supplied: JSON? = nil) {
        var target = supplied ?? menuTargets(nil); let ids = target["document_ids"] as? [String] ?? []; guard !ids.isEmpty, let window else { return }
        Task {
            do {
                guard try await prepareSavedAction(documentIDs:ids,purpose:"导出") else { return }
                if ids.count == 1, selectedDocument?.id == ids[0], !viewingHistorical { target["version"] = selectedDocument?.version }
                guard NSApp.activationPolicy() != .prohibited else { return }
                let alert = NSAlert(); alert.messageText = "导出 \(ids.count) 份文档"
                let format = NSPopUpButton(frame:NSRect(x:0,y:0,width:380,height:30)); format.addItems(withTitles:["完整离线 HTML 包（ZIP）","整份正文（Markdown）","整份正文（TXT）","当前页正文（Markdown）","AI 阅读附件（正文与原始 HTML）","当前页独立 HTML（资源内联）","完整离线 HTML 目录"])
                alert.accessoryView = format; alert.addButton(withTitle:"继续"); alert.addButton(withTitle:"取消")
                guard await alert.beginSheetModal(for:window) == .alertFirstButtonReturn else { return }
                let choice = format.indexOfSelectedItem
                if ids.count > 1 {
                    if choice == 3 || choice >= 5 { throw EngineFailure("export_scope","当前页或目录请选择一份文档；批量导出可使用完整 ZIP、整份正文或 AI 附件。") }
                    let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.canCreateDirectories = true; panel.prompt = "导出到这里"
                    guard await panel.beginSheetModal(for:window) == .OK, let folder = panel.url else { return }
                    var success = 0, failures: [String] = []
                    for id in ids { do { let doc = try await currentDocument(id); let name = safeFilename(doc.title)+"-"+String(id.suffix(6)); let suffix = choice == 1 || choice == 3 ? ".md" : choice == 2 ? ".txt" : ".zip"; _ = try await exportOne(id:id,target:target,choice:choice,output:folder.appendingPathComponent(name+suffix)); success += 1 } catch { failures.append(id+"："+error.localizedDescription) } }
                    status.stringValue = "导出：\(success) 份成功，\(failures.count) 份失败 · \(folder.path)"; status.toolTip = failures.joined(separator:"\n")
                } else {
                    let doc = try await currentDocument(ids[0]); let panel = NSSavePanel(); panel.canCreateDirectories = true
                    let suffix = choice == 1 || choice == 3 ? ".md" : choice == 2 ? ".txt" : choice == 5 ? ".html" : choice == 6 ? "-HTML" : ".zip"; panel.nameFieldStringValue = safeFilename(doc.title)+suffix
                    guard await panel.beginSheetModal(for:window) == .OK, let output = panel.url else { return }
                    _ = try await exportOne(id:ids[0],target:target,choice:choice,output:output)
                    status.stringValue = "已导出 · " + output.path; status.toolTip = output.path
                }
            } catch { showError(error) }
        }
    }
    func safeFilename(_ title: String) -> String { String(title.map { "/:\n\r\0".contains($0) ? "_" : $0 }.prefix(150)) }
    func exportOne(id: String,target: JSON,choice: Int,output: URL) async throws -> JSON {
        var params: JSON = ["document_id":id,"output":output.path]
        if let version = target["version"], (target["document_ids"] as? [String])?.count == 1 { params["version"] = version }
        if choice == 0 { return try await engine.call("export",params) }
        if choice == 5 { if let page = target["page_id"] { params["page_id"] = page }; return try await engine.call("export-page",params,timeout:75) }
        if choice == 6 { return try await engine.call("export-folder",params,timeout:75) }
        params["scope"] = choice == 3 ? "page" : "document"; params["format"] = choice == 2 ? "text" : "markdown"
        if choice == 3, let page = target["page_id"] { params["page_id"] = page }
        if choice == 4 { params["mode"] = "attachment"; return try await engine.call("prepare-ai",params) }
        return try await engine.call("export-text",params)
    }
    func tableView(_ tableView: NSTableView, pasteboardWriterForRow row: Int) -> NSPasteboardWriting? {
        guard tableView === documentsTable else { return nil }
        let id = matches.isEmpty ? (documents.indices.contains(row) ? documents[row].id : nil) : (matches.indices.contains(row) ? matches[row].documentID : nil)
        guard let id, let doc = documents.first(where:{ $0.id == id }) else { return nil }
        let provider = NSFilePromiseProvider(fileType:UTType.zip.identifier,delegate:self); provider.userInfo = ["document_id":id,"title":doc.title,"version":doc.version]; return provider
    }
    func filePromiseProvider(_ filePromiseProvider: NSFilePromiseProvider, fileNameForType fileType: String) -> String { safeFilename((filePromiseProvider.userInfo as? JSON)?["title"] as? String ?? "Lumen")+".zip" }
    func filePromiseProvider(_ filePromiseProvider: NSFilePromiseProvider, writePromiseTo url: URL, completionHandler: @escaping (Error?) -> Void) {
        guard let info = filePromiseProvider.userInfo as? JSON, let id = info["document_id"] as? String else { completionHandler(EngineFailure("document_missing","拖出文档信息缺失")); return }
        Task { do { _ = try await engine.call("export",["document_id":id,"version":info["version"] as? Int ?? 1,"output":url.path]); completionHandler(nil) } catch { completionHandler(error) } }
    }
    func tableView(_ tableView: NSTableView, validateDrop info: NSDraggingInfo, proposedRow row: Int, proposedDropOperation dropOperation: NSTableView.DropOperation) -> NSDragOperation {
        guard tableView === documentsTable, info.draggingPasteboard.canReadObject(forClasses:[NSURL.self],options:[.urlReadingFileURLsOnly:true]) else { return [] }; return .copy
    }
    func tableView(_ tableView: NSTableView, acceptDrop info: NSDraggingInfo, row: Int, dropOperation: NSTableView.DropOperation) -> Bool {
        guard tableView === documentsTable, let urls = info.draggingPasteboard.readObjects(forClasses:[NSURL.self],options:[.urlReadingFileURLsOnly:true]) as? [URL], !urls.isEmpty else { return false }
        Task { for url in urls { await importURL(url) } }; return true
    }
}
