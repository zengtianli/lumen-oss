import AppKit
import WebKit
import CryptoKit

@MainActor enum NativeSelfTest {
    static func js(_ reader: WKWebView, _ script: String) async throws -> Any? {
        try await withCheckedThrowingContinuation { continuation in
            reader.evaluateJavaScript(script) { value, error in
                if let error {
                    let detail = (error as NSError).userInfo["WKJavaScriptExceptionMessage"] as? String ?? error.localizedDescription
                    continuation.resume(throwing: EngineFailure("javascript_failed", detail))
                } else { continuation.resume(returning: value) }
            }
        }
    }
    static func waitForPage(_ reader: WKWebView) async throws {
        let deadline = Date().addingTimeInterval(12)
        while Date() < deadline {
            if let ready = try? await js(reader, "Boolean(window.LumenReader && document.querySelector('[data-lumen-source]'))"), ready as? Bool == true { return }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw EngineFailure("reader_timeout", "离屏真实阅读器未完成加载。")
    }
    static func waitForDocument(_ controller: LibraryWindowController) async throws {
        guard let document = controller.selectedDocument else { throw EngineFailure("document_missing", "阅读器未选中成果。") }
        let identity = try LumenPaths.string(["id": document.id, "version": document.version, "page": controller.selectedPage?.id ?? ""])
        let deadline = Date().addingTimeInterval(12)
        while Date() < deadline {
            if controller.reader.isLoading { try await Task.sleep(nanoseconds:100_000_000); continue }
            if let ready = try? await js(controller.reader, "(()=>{const d=\(identity);if(!window.LumenReader)return false;const s=window.LumenReader.snapshot();return s.document_id===d.id && s.expected_version===d.version && s.page_id===d.page})()"), ready as? Bool == true { return }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw EngineFailure("reader_timeout", "实际页面没有加载到选中版本。")
    }
    static func saveAndReopen(_ controller: LibraryWindowController, script: String = "window.LumenReader.save();true") async throws {
        guard let document = controller.selectedDocument, let page = controller.selectedPage else { throw EngineFailure("document_missing", "未选择可保存的页面。") }
        _ = try await js(controller.reader, script)
        let deadline = Date().addingTimeInterval(15)
        while Date() < deadline {
            let result = try await controller.engine.call("get", ["document_id": document.id])
            if ((result["document"] as? JSON)?["version"] as? Int ?? 0) > document.version {
                await controller.open(documentID: document.id, pageID: page.id); try await waitForDocument(controller); return
            }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw EngineFailure("save_timeout", "页面保存未返回新版本：" + controller.status.stringValue)
    }
    static func waitForMetadata(_ controller: LibraryWindowController, after revision: Int) async throws {
        let deadline = Date().addingTimeInterval(12)
        while Date() < deadline {
            if (controller.selectedDocument?.metadataRevision ?? 0) > revision { return }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw EngineFailure("metadata_timeout", "笔记与信息未完成保存：" + controller.status.stringValue)
    }
    static func draftProtection(_ controller: LibraryWindowController) async throws -> [String: Bool] {
        var checks: [String: Bool] = [:]
        let content: JSON = ["title": "草稿保护验收", "pages": [["id": "draft", "title": "草稿页面", "blocks": [["id": "body", "type": "prose", "text": "保存正文时也保护个人信息。"]]]]]
        let result = try await controller.engine.call("build", ["content": content, "kind": "portal"])
        guard let document = (result["document"] as? JSON).flatMap(KnowledgeDocument.init) else { throw EngineFailure("fixture_failed", "草稿验收没有返回成果。") }
        await controller.open(documentID: document.id, pageID: "draft"); try await waitForDocument(controller)
        controller.titleField.stringValue = "未保存的成果标题"; controller.projectField.stringValue = "草稿项目"; controller.tagsField.stringValue = "草稿标签, 验收"; controller.notes.string = "未保存的个人笔记"
        controller.controlTextDidChange(Notification(name: NSControl.textDidChangeNotification, object: controller.titleField))
        controller.textDidChange(Notification(name: NSText.didChangeNotification, object: controller.notes))
        let revision = controller.selectedDocument?.metadataRevision ?? 0
        controller.toggleFavorite(); try await waitForMetadata(controller, after: revision)
        checks["metadata_favorite_keeps_all_drafts"] = controller.titleField.stringValue == "未保存的成果标题" && controller.projectField.stringValue == "草稿项目" && controller.tagsField.stringValue.contains("草稿标签") && controller.notes.string == "未保存的个人笔记" && controller.selectedDocument?.favorite == true
        let beforeArchive = controller.selectedDocument?.metadataRevision ?? 0
        controller.toggleArchive(); try await waitForMetadata(controller, after: beforeArchive)
        checks["metadata_archive_keeps_drafts"] = controller.notes.string == "未保存的个人笔记" && controller.titleField.stringValue == "未保存的成果标题"
        _ = try await js(controller.reader, "(()=>{window.LumenReader.setEditing(true);const p=document.querySelector('[data-lumen-source=\"/pages/0/blocks/0/text\"]');p.textContent='正文保存后草稿仍保留';p.dispatchEvent(new Event('input',{bubbles:true}));return true})()")
        try await saveAndReopen(controller)
        checks["metadata_content_save_keeps_drafts"] = controller.notes.string == "未保存的个人笔记" && controller.titleField.stringValue == "未保存的成果标题"
        let another = try await controller.engine.call("build", ["content": ["title": "另一个成果", "pages": [["id": "other", "title": "另一页", "blocks": [["id": "body", "type": "prose", "text": "用于切换。"]]]]], "kind": "portal"])
        guard let otherID = (another["document"] as? JSON)?["id"] as? String else { throw EngineFailure("fixture_failed", "切换验收没有返回成果。") }
        await controller.open(documentID: otherID); await controller.open(documentID: document.id); try await waitForDocument(controller)
        checks["metadata_switch_restores_drafts"] = controller.titleField.stringValue == "未保存的成果标题" && controller.notes.string == "未保存的个人笔记"
        checks["metadata_close_captures_drafts"] = controller.window.map { controller.windowShouldClose($0) } == true
        try await controller.persistPosition()
        let reopened = LibraryWindowController(engine: controller.engine)
        await reopened.open(documentID: document.id); try await waitForDocument(reopened)
        checks["metadata_exit_reopen_restores_drafts"] = reopened.titleField.stringValue == "未保存的成果标题" && reopened.projectField.stringValue == "草稿项目" && reopened.tagsField.stringValue.contains("草稿标签") && reopened.notes.string == "未保存的个人笔记"
        let beforeSave = controller.selectedDocument?.metadataRevision ?? 0
        controller.saveMetadata(); try await waitForMetadata(controller, after: beforeSave)
        let saved = try await controller.engine.call("get", ["document_id": document.id])
        checks["metadata_explicit_save_and_reopen"] = (saved["document"] as? JSON)?["notes"] as? String == "未保存的个人笔记" && (saved["document"] as? JSON)?["title"] as? String == "未保存的成果标题"
        let clean = LibraryWindowController(engine: controller.engine)
        await clean.open(documentID: document.id); try await waitForDocument(clean)
        checks["metadata_saved_draft_cleared"] = !clean.versionLabel.stringValue.contains("有草稿") && clean.notes.string == "未保存的个人笔记"
        controller.notes.string = "异步保存并快速切换后的笔记"; controller.captureMetadataDraft()
        let saving = Task { await controller.applyMetadata(["notes": "异步保存并快速切换后的笔记"], id: document.id) }
        await Task.yield(); await controller.open(documentID: otherID); await saving.value
        await controller.open(documentID: document.id); try await waitForDocument(controller)
        checks["metadata_async_save_switch_acknowledges_original_draft"] = controller.notes.string == "异步保存并快速切换后的笔记" && !controller.versionLabel.stringValue.contains("有草稿")
        controller.notes.string = "发生冲突时的本机草稿"; controller.captureMetadataDraft()
        guard let current = controller.selectedDocument else { throw EngineFailure("document_missing", "冲突验收未选中成果。") }
        _ = try await controller.engine.call("metadata", ["document_id": current.id, "expected_version": current.version, "expected_metadata_revision": current.metadataRevision, "notes": "其他客户端已保存的笔记"])
        await controller.open(documentID: document.id); try await waitForDocument(controller)
        let externalRevision = controller.selectedDocument?.metadataRevision ?? 0
        controller.toggleFavorite(); try await waitForMetadata(controller, after: externalRevision)
        controller.saveMetadata()
        let conflictDeadline = Date().addingTimeInterval(12)
        while Date() < conflictDeadline && !controller.status.stringValue.contains("其他窗口修改") { try await Task.sleep(nanoseconds: 100_000_000) }
        let concurrent = try await controller.engine.call("get", ["document_id": document.id])
        checks["metadata_conflict_retains_original_token_and_draft"] = controller.notes.string == "发生冲突时的本机草稿" && (concurrent["document"] as? JSON)?["notes"] as? String == "其他客户端已保存的笔记" && controller.versionLabel.stringValue.contains("有草稿") && controller.status.stringValue.contains("其他窗口修改")
        let afterConflict = LibraryWindowController(engine: controller.engine)
        await afterConflict.open(documentID: document.id); try await waitForDocument(afterConflict)
        checks["metadata_conflicted_draft_survives_reopen"] = afterConflict.notes.string == "发生冲突时的本机草稿"
        return checks
    }
    static func extraReaders(_ controller: LibraryWindowController) async throws -> [String: Bool] {
        var checks: [String: Bool] = [:]
        let incoming = controller.engine.stateDirectory.appendingPathComponent("acceptance-input", isDirectory: true)
        try FileManager.default.createDirectory(at: incoming, withIntermediateDirectories: true)
        let beforeTarget = String(repeating: "<p>前置阅读正文，用于验证搜索实际滚动到命中段落。</p>", count: 40)
        try Data("<!doctype html><meta charset='utf-8'><title>旧知识</title><h1 id='legacy-title'>旧 HTML 标题</h1><article><p id='legacy-body'>旧 HTML 正文</p><a href='chapter.html'>下一章</a>\(beforeTarget)<p data-feedback-block='ordinary-search-target'>原生搜索段落定位验收。</p><div style='height:1200px'>后续阅读空间。</div></article>".utf8).write(to: incoming.appendingPathComponent("index.html"))
        try Data("<!doctype html><meta charset='utf-8'><title>下一章</title><h1>下一章</h1><p>旧成果中的下一章。</p>".utf8).write(to: incoming.appendingPathComponent("chapter.html"))
        await controller.importURL(incoming.appendingPathComponent("index.html")); try await waitForDocument(controller)
        guard let ordinary = controller.selectedDocument else { throw EngineFailure("import_failed", "普通 HTML 未收录。") }
        checks["ordinary_gui_import_resources"] = controller.pages.count == 2
        _ = try await js(controller.reader, "window.LumenReader.setEditing(true);const h=document.getElementById('legacy-title');h.textContent='保存后的旧 HTML 标题';h.dispatchEvent(new Event('input',{bubbles:true}));const p=document.getElementById('legacy-body');p.textContent='保存后的旧 HTML 正文';p.dispatchEvent(new Event('input',{bubbles:true}));window.LumenReader.addComment(p.dataset.feedbackBlock,'旧页面意见也持久保存');true")
        try await saveAndReopen(controller)
        let ordinaryBody = try await js(controller.reader, "document.body.innerText") as? String ?? ""
        checks["ordinary_title_body_save_reopen"] = ordinaryBody.contains("保存后的旧 HTML 标题") && ordinaryBody.contains("保存后的旧 HTML 正文")
        let ordinaryFeedback = try await controller.engine.call("feedback", ["document_id": ordinary.id])
        checks["ordinary_comment_save_reopen"] = (try LumenPaths.string(ordinaryFeedback)).contains("旧页面意见也持久保存")
        controller.searchField.stringValue = "原生搜索段落定位验收"; await controller.refresh()
        guard let searchIndex = controller.matches.firstIndex(where: { $0.documentID == ordinary.id && $0.anchor == "ordinary-search-target" }) else { throw EngineFailure("search_anchor_missing", "搜索未返回命中段落的稳定定位。") }
        controller.documentsTable.selectRowIndexes(IndexSet(integer: searchIndex), byExtendingSelection: false)
        let searchDeadline = Date().addingTimeInterval(12)
        var searchLocated = false
        while Date() < searchDeadline {
            searchLocated = (try? await js(controller.reader, "(()=>{const e=Array.from(document.querySelectorAll('[data-feedback-block]')).find(e=>e.dataset.feedbackBlock==='ordinary-search-target');if(!e||e.id)return false;const r=e.getBoundingClientRect(),maxScroll=Math.max(0,document.documentElement.scrollHeight-innerHeight),expected=Math.min(r.top+scrollY,maxScroll);return scrollY>500&&Math.abs(scrollY-expected)<80&&r.top>=-80&&r.top<innerHeight})()")) as? Bool == true
            if searchLocated { break }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        checks["production_search_feedback_anchor_scroll"] = searchLocated
        controller.searchField.stringValue = ""; await controller.refresh()
        let legacyChapter = URL(fileURLWithPath: ordinary.entryPath).deletingLastPathComponent().appendingPathComponent("chapter.html")
        checks["legacy_base_maps_current_version"] = controller.currentPageForLegacyURL(legacyChapter)?.absolutePath.contains("/versions/2/site/") == true
        _ = try await js(controller.reader, "let b=document.createElement('base');b.href='\(URL(fileURLWithPath: ordinary.entryPath).deletingLastPathComponent().absoluteString)';document.head.prepend(b);document.querySelector('a').click();true")
        let navDeadline = Date().addingTimeInterval(5)
        while Date() < navDeadline && controller.reader.url?.lastPathComponent != "chapter.html" { try await Task.sleep(nanoseconds: 100_000_000) }
        checks["legacy_base_navigation_current_version"] = controller.reader.url?.path.contains("/versions/2/site/") == true && controller.reader.url?.lastPathComponent == "chapter.html"
        if let first = controller.pages.first(where: { $0.relativePath == "index.html" }) { await controller.loadPage(first); try await waitForDocument(controller) }
        _ = try await js(controller.reader, "(()=>{const e=document.createElement('div');e.id='large-payload';e.hidden=true;e.textContent='x'.repeat(1200000);document.body.append(e);return true})()")
        try await saveAndReopen(controller)
        checks["large_html_native_rpc_file_save"] = (try await js(controller.reader, "document.getElementById('large-payload').textContent.length") as? Int ?? 0) == 1_200_000

        let nested = incoming.appendingPathComponent("nested-site", isDirectory: true)
        try FileManager.default.createDirectory(at: nested.appendingPathComponent("chapters"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: nested.appendingPathComponent("assets"), withIntermediateDirectories: true)
        try Data("<!doctype html><meta charset='utf-8'><title>子目录入口</title><a href='chapters/nested.html'>打开子目录章节</a>".utf8).write(to: nested.appendingPathComponent("index.html"))
        try Data("<!doctype html><meta charset='utf-8'><title>子目录章节</title><link rel='stylesheet' href='../assets/nested.css'><script src='../assets/nested.js' defer></script><div id='nested-resource'>资源验收</div><a href='../index.html'>返回入口</a>".utf8).write(to: nested.appendingPathComponent("chapters/nested.html"))
        try Data("#nested-resource{width:137px}".utf8).write(to: nested.appendingPathComponent("assets/nested.css"))
        try Data("document.getElementById('nested-resource').dataset.scriptLoaded='yes'".utf8).write(to: nested.appendingPathComponent("assets/nested.js"))
        await controller.importURL(nested); try await waitForDocument(controller)
        guard let nestedPage = controller.pages.first(where: { $0.relativePath == "chapters/nested.html" }) else { throw EngineFailure("fixture_failed", "子目录章节未收录。") }
        await controller.loadPage(nestedPage); try await waitForDocument(controller)
        checks["nested_chapter_parent_css_loaded"] = (try await js(controller.reader, "getComputedStyle(document.getElementById('nested-resource')).width") as? String) == "137px"
        checks["nested_chapter_parent_script_loaded"] = (try await js(controller.reader, "document.getElementById('nested-resource').dataset.scriptLoaded") as? String) == "yes"
        _ = try await js(controller.reader, "document.querySelector('a').click();true")
        let nestedDeadline = Date().addingTimeInterval(5)
        while Date() < nestedDeadline && controller.reader.url?.lastPathComponent != "index.html" { try await Task.sleep(nanoseconds: 100_000_000) }
        checks["nested_chapter_navigation_stays_in_site"] = controller.reader.url?.lastPathComponent == "index.html"
        let missing = incoming.appendingPathComponent("missing-resource.html")
        try Data("<!doctype html><meta charset='utf-8'><title>缺失资源提示验收</title><img src='not-present.png'><p>正文仍可阅读。</p>".utf8).write(to: missing)
        await controller.importURL(missing); try await waitForDocument(controller)
        checks["import_incomplete_resources_visible_warning"] = controller.status.stringValue.contains("部分资源缺失") && controller.status.toolTip?.contains("not-present.png") == true
        await controller.importURL(missing); try await waitForDocument(controller)
        checks["import_reused_source_preserves_notice"] = controller.status.stringValue.contains("已有这份来源") && controller.status.stringValue.contains("保留修改")

        let graphSource = incoming.appendingPathComponent("graph", isDirectory: true)
        try FileManager.default.createDirectory(at: graphSource, withIntermediateDirectories: true)
        try Data("# 项目说明\n生成、阅读、编辑。".utf8).write(to: graphSource.appendingPathComponent("README.md"))
        let graphResult = try await controller.engine.call("convert", ["source": graphSource.path, "kind": "graph"])
        guard let graph = (graphResult["document"] as? JSON).flatMap(KnowledgeDocument.init) else { throw EngineFailure("graph_failed", "目录图谱没有返回成果。") }
        await controller.open(documentID: graph.id); try await waitForDocument(controller)
        _ = try await js(controller.reader, "window.LumenReader.setEditing(true);const h=document.getElementById('detail-title');h.textContent='图谱修改后的名称';h.dispatchEvent(new Event('input',{bubbles:true}));const p=document.getElementById('description');p.textContent='图谱修改后的解释';p.dispatchEvent(new Event('input',{bubbles:true}));window.LumenReader.addComment('graph-node:home:object','节点的补充意见');true")
        try await saveAndReopen(controller)
        let graphData = try await js(controller.reader, "document.getElementById('data').textContent") as? String ?? ""
        checks["graph_name_description_save_reopen"] = graphData.contains("图谱修改后的名称") && graphData.contains("图谱修改后的解释")
        let graphFeedback = try await controller.engine.call("feedback", ["document_id": graph.id])
        checks["graph_node_comment_save_reopen"] = (try LumenPaths.string(graphFeedback)).contains("节点的补充意见")

        let executable = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        let bundled = executable.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("Resources/Acceptance/reader.pptx")
        let development = executable.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("Resources/Acceptance/reader.pptx")
        let pptSource = FileManager.default.fileExists(atPath: bundled.path) ? bundled : development
        let before = SHA256.hash(data: try Data(contentsOf: pptSource))
        let copied = incoming.appendingPathComponent("reader.pptx"); try FileManager.default.copyItem(at: pptSource, to: copied)
        await controller.importURL(copied); try await waitForDocument(controller)
        guard let ppt = controller.selectedDocument else { throw EngineFailure("ppt_failed", "课件未生成并收录。") }
        checks["ppt_gui_convert_original_pages"] = (try await js(controller.reader, "document.querySelectorAll('.slide').length") as? Int ?? 0) == 3
        let editPPT = "window.LumenReader.setEditing(true);const t=document.querySelector('.media-context');t.textContent='原生课件说明编辑保存';t.dispatchEvent(new Event('input',{bubbles:true}));const c=document.querySelector('table td');c.textContent='单元格保存值';c.dispatchEvent(new Event('input',{bubbles:true}));imageTarget=document.querySelector('.photo');const b=atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZQAAAABJRU5ErkJggg==');const bytes=Uint8Array.from(b,x=>x.charCodeAt(0));document.getElementById('replace-image').onchange({target:{files:[new File([bytes],'replacement.png',{type:'image/png'})],value:''}});true"
        _ = try await js(controller.reader, editPPT); try await Task.sleep(nanoseconds: 300_000_000)
        try await saveAndReopen(controller, script: "document.getElementById('save').click();true")
        let pptText = try await js(controller.reader, "document.querySelector('.media-context').textContent") as? String ?? ""
        let cellText = try await js(controller.reader, "document.querySelector('table td').textContent") as? String
        checks["ppt_native_text_table_save_reopen"] = pptText.contains("原生课件说明编辑保存") && cellText == "单元格保存值"
        checks["ppt_native_image_replacement_save_reopen"] = (try await js(controller.reader, "document.querySelector('.photo').getAttribute('src')") as? String ?? "").hasPrefix("data:image/png;base64,")
        checks["ppt_source_unchanged"] = SHA256.hash(data: try Data(contentsOf: pptSource)) == before
        _ = ppt
        return checks
    }
    static func snapshot(_ controller: LibraryWindowController, to url: URL, appearance: NSAppearance.Name) async throws {
        guard NSApp.activationPolicy() == .prohibited else { throw EngineFailure("offscreen_required", "截图激活策略不是禁止：\(NSApp.activationPolicy().rawValue)") }
        guard let window = controller.window, let frame = window.contentView?.superview else { throw EngineFailure("offscreen_frame_missing", "真实离屏窗口外框尚未构造。") }
        window.appearance = NSAppearance(named: appearance); frame.layoutSubtreeIfNeeded()
        let config = WKSnapshotConfiguration(); config.afterScreenUpdates = false
        let pageImage: NSImage = try await withCheckedThrowingContinuation { continuation in
            controller.reader.takeSnapshot(with: config) { image, error in if let image { continuation.resume(returning: image) } else { continuation.resume(throwing: error ?? EngineFailure("snapshot_failed", "阅读器截图失败。")) } }
        }
        guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(frame.bounds.width * 2), pixelsHigh: Int(frame.bounds.height * 2), bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0), let context = NSGraphicsContext(bitmapImageRep: bitmap) else { throw EngineFailure("snapshot_failed", "原生窗口渲染失败。") }
        bitmap.size = frame.bounds.size
        NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = context
        window.effectiveAppearance.performAsCurrentDrawingAppearance { NSColor.windowBackgroundColor.setFill(); frame.bounds.fill() }
        NSGraphicsContext.restoreGraphicsState()
        // WKWebView's remote layer may cache at an unrelated origin in an offscreen AppKit frame.
        // Render native chrome without that layer, then composite its own production snapshot exactly once.
        let previousAlpha = controller.reader.alphaValue; controller.reader.alphaValue = 0
        frame.cacheDisplay(in: frame.bounds, to: bitmap)
        controller.reader.alphaValue = previousAlpha
        NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = context
        let rect = controller.reader.convert(controller.reader.bounds, to: frame)
        // cacheDisplay uses view coordinates; NSImage drawing uses an unscaled bitmap context.
        // Draw with explicit pixel coordinates to match the 2x chrome bitmap.
        if let cgImage = pageImage.cgImage(forProposedRect: nil, context: nil, hints: nil) {
            let cg = context.cgContext
            cg.saveGState(); cg.scaleBy(x: 2, y: 2)
            cg.draw(cgImage, in: rect); cg.restoreGState()
        }
        NSGraphicsContext.restoreGraphicsState()
        guard let data = bitmap.representation(using: .png, properties: [:]), data.count > 4_000 else { throw EngineFailure("snapshot_empty", "窗口截图没有有效内容。") }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
    }

    static func run(_ controller: LibraryWindowController) async {
        var checks: [String: Bool] = [:], evidence: JSON = [:]
        let output = ProcessInfo.processInfo.environment["LUMEN_UI_OUT"].map { URL(fileURLWithPath: $0, isDirectory: true) } ?? LumenPaths.stateDirectory.appendingPathComponent("native-evidence")
        do {
            try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
            if CommandLine.arguments.contains("--management-self-test") {
                checks = try await managementAndAI(controller,output:output)
                finish(controller,checks:checks,evidence:["state_dir":controller.engine.stateDirectory.path,"scope":"production management and AI material actions","unverified":["实际鼠标拖动和实体键盘输入；通过生产动作和专用测试剪贴板验证，不触碰用户剪贴板。"]],output:output)
                return
            }
            guard let window = controller.window else { throw EngineFailure("window_missing", "生产窗口不存在。") }
            checks["native_title_visible"] = window.title == LumenPaths.displayName && window.titleVisibility == .visible && !window.titlebarAppearsTransparent
            checks["native_window_controls"] = [.titled, .closable, .miniaturizable, .resizable].allSatisfy { window.styleMask.contains($0) } && window.standardWindowButton(.closeButton) != nil && window.standardWindowButton(.miniaturizeButton) != nil && window.standardWindowButton(.zoomButton) != nil
            checks["native_window_movable"] = window.isMovable && !window.styleMask.contains(.fullSizeContentView)
            checks["native_menu_wiring"] = NSApp.mainMenu?.items.contains { $0.submenu?.title == "文件" } == true && NSApp.windowsMenu != nil
            checks["native_sidebar_list_search"] = controller.sidebar.numberOfColumns == 1 && controller.documentsTable.numberOfColumns == 1 && controller.searchField.placeholderString != nil
            let content: JSON = ["title": "Lumen 阅读与保存验收", "subtitle": "生产窗口 · 真实引擎 · 离屏运行", "pages": [["id": "overview", "title": "理解成果", "summary": "把 agent 的解释积累为自己的知识。", "blocks": [["id": "body", "type": "prose", "text": "可编辑的原始正文。"], ["id": "steps", "type": "steps", "items": [["title": "生成 HTML", "text": "按标准构建"], ["title": "阅读和修改", "text": "积累理解"], ["title": "保存并重开", "text": "随时继续"]]]]]]]
            let result = try await controller.engine.call("build", ["content": content, "kind": "portal", "project": "Native acceptance", "tags": ["知识库", "验收"]])
            guard let value = result["document"] as? JSON, let document = KnowledgeDocument(value) else { throw EngineFailure("fixture_failed", "知识构建未返回文档。") }
            await controller.refresh(); await controller.open(documentID: document.id, pageID: "overview")
            try await waitForPage(controller.reader)
            checks["production_gui_engine_build_read"] = controller.selectedDocument?.id == document.id && controller.pages.count >= 1 && controller.reader.url?.isFileURL == true
            let loaded = try await js(controller.reader, "document.body.innerText") as? String ?? ""
            checks["production_reader_real_content"] = loaded.contains("理解成果") && loaded.contains("可编辑的原始正文")
            let edit: String = "window.LumenReader.setEditing(true); const title=document.querySelector('[data-lumen-source=\"/pages/0/title\"]');title.textContent='已经理解的成果';title.dispatchEvent(new Event('input',{bubbles:true})); const body=document.querySelector('[data-lumen-source=\"/pages/0/blocks/0/text\"]');body.textContent='原生阅读器编辑后保存的正文。';body.dispatchEvent(new Event('input',{bubbles:true}));window.LumenReader.addComment(body.dataset.feedbackBlock,'这里补充我的认识'); JSON.stringify(window.LumenReader.snapshot())"
            guard let payload = try await js(controller.reader, edit) as? String, let params = try JSONSerialization.jsonObject(with: Data(payload.utf8)) as? JSON else { throw EngineFailure("editor_failed", "真实编辑器没有保存载荷。") }
            checks["production_reader_edit_patches"] = (params["patches"] as? [JSON] ?? []).count >= 2
            // Use the production WKScriptMessageHandler path, not a special save substitute.
            _ = try await js(controller.reader, "window.LumenReader.save();true")
            let saveDeadline = Date().addingTimeInterval(15)
            var reopened: JSON = [:]
            while Date() < saveDeadline {
                reopened = try await controller.engine.call("get", ["document_id": document.id])
                if (reopened["document"] as? JSON)?["version"] as? Int ?? 0 > document.version { break }
                try await Task.sleep(nanoseconds: 100_000_000)
            }
            let updated = reopened["document"] as? JSON ?? [:]
            checks["production_bridge_save_new_version"] = (updated["version"] as? Int ?? 0) > document.version
            await controller.open(documentID: document.id, pageID: "overview"); try await waitForPage(controller.reader)
            let savedText = try await js(controller.reader, "document.body.innerText") as? String ?? ""
            checks["production_reader_save_reload"] = savedText.contains("已经理解的成果") && savedText.contains("原生阅读器编辑后保存的正文")
            let feedback = try await controller.engine.call("feedback", ["document_id": document.id, "page_id": "overview"])
            checks["production_feedback_persisted"] = (try LumenPaths.string(feedback)).contains("这里补充我的认识")
            if let current = controller.selectedDocument {
                await controller.applyMetadata(["document_id": document.id, "expected_version": current.version, "favorite": true, "notes": "离屏原生界面的个人笔记"], id: document.id)
            }
            let metadata = try await controller.engine.call("get", ["document_id": document.id])
            checks["production_gui_notes_favorite"] = (metadata["document"] as? JSON)?["favorite"] as? Bool == true && (metadata["document"] as? JSON)?["notes"] as? String == "离屏原生界面的个人笔记"
            controller.searchField.stringValue = "原生阅读器编辑后保存"; await controller.refresh()
            checks["production_gui_search_chapter"] = controller.matches.contains { $0.documentID == document.id && $0.pageID == "overview" }
            controller.searchField.stringValue = ""; await controller.refresh()
            _ = try await controller.engine.call("position", ["document_id": document.id, "page_id": "overview", "scroll_y": 112])
            let position = try await controller.engine.call("position", ["document_id": document.id])
            checks["production_position_readback"] = (position["position"] as? JSON)?["page_id"] as? String == "overview"
            await controller.loadVersions(); checks["production_versions_list"] = controller.versions.count >= 2
            if let current = controller.selectedDocument { await controller.restore(version: document.version, document: current) }
            let restored = try await controller.engine.call("get", ["document_id": document.id])
            let restoredContent = try LumenPaths.string(restored["content"] ?? [:])
            checks["production_gui_restore_new_version"] = ((restored["document"] as? JSON)?["version"] as? Int ?? 0) > (updated["version"] as? Int ?? 0) && restoredContent.contains("可编辑的原始正文")
            await controller.open(documentID: document.id, pageID: "overview"); try await waitForPage(controller.reader)
            let originalSettings = try await controller.engine.call("settings")
            _ = try await controller.engine.call("settings", ["values": ["labels": ["generate": "标准化生成验收"]]])
            await controller.refresh(); checks["hot_labels_runtime"] = controller.generationLabel == "标准化生成验收"
            try Data("broken-json".utf8).write(to: controller.engine.stateDirectory.appendingPathComponent("settings.json"), options: .atomic)
            await controller.refresh(); checks["hot_labels_broken_fallback"] = controller.generationLabel != "标准化生成验收" && !controller.documents.isEmpty
            _ = try await controller.engine.call("settings", ["values": originalSettings["settings"] as? JSON ?? [:]])
            await controller.refresh(); await ProductLifecycle.settings?.load()
            checks["settings_read_same_engine"] = ProductLifecycle.settings?.font.indexOfSelectedItem == 5 && ProductLifecycle.settings?.restore.state == .on
            let guidance = try await controller.engine.call("guide", ["topic": "overview"])
            checks["native_generation_standard_guide"] = !(guidance["body"] as? String ?? "").isEmpty
            checks.merge(try await extraReaders(controller)) { a, b in a && b }
            checks.merge(try await draftProtection(controller)) { a, b in a && b }
            checks.merge(try await managementAndAI(controller,output:output)) { a,b in a && b }
            await controller.open(documentID: document.id, pageID: "overview"); try await waitForDocument(controller)
            try await controller.persistPosition()
            let exitPosition = try await controller.engine.call("position", ["document_id": document.id])
            checks["exit_waits_for_position_rpc"] = (exitPosition["position"] as? JSON)?["page_id"] as? String == "overview"
            await controller.loadVersions()
            controller.setInspectorVisible(true); controller.window?.contentView?.layoutSubtreeIfNeeded()
            try await snapshot(controller, to: output.appendingPathComponent("library-light.png"), appearance: .aqua)
            try await snapshot(controller, to: output.appendingPathComponent("library-dark.png"), appearance: .darkAqua)
            let settingsLight = try AppLifecycleUI.shared.offscreenSnapshot(to: output.appendingPathComponent("settings-light.png"), appearance: .aqua)
            let settingsDark = try AppLifecycleUI.shared.offscreenSnapshot(to: output.appendingPathComponent("settings-dark.png"), appearance: .darkAqua)
            checks.merge(settingsLight) { a, b in a && b }; checks.merge(settingsDark) { a, b in a && b }
            checks["quiet_no_window_dock_focus"] = NSApp.activationPolicy() == .prohibited && !NSApp.isActive && NSApp.windows.allSatisfy { !$0.isVisible && !$0.isKeyWindow }
            evidence = ["state_dir": controller.engine.stateDirectory.path, "document_id": document.id, "reader_bounds": NSStringFromRect(controller.reader.bounds), "reader_frame": NSStringFromRect(controller.reader.convert(controller.reader.bounds, to: window.contentView?.superview)), "window_frame_bounds": NSStringFromRect(window.contentView?.superview?.bounds ?? .zero), "snapshots": ["library-light.png", "library-dark.png", "settings-light.png", "settings-dark.png"], "unverified": ["实际鼠标拖动、红黄绿按钮点击与实体键盘输入；验收直接调用生产动作，不合成用户输入。"]]
        } catch { checks["completed"] = false; evidence["error"] = error.localizedDescription }
        finish(controller,checks:checks,evidence:evidence,output:output)
    }
    static func finish(_ controller:LibraryWindowController,checks:[String:Bool],evidence:JSON,output:URL) {
        let report: JSON = ["ok": !checks.isEmpty && checks.values.allSatisfy { $0 }, "checks": checks, "evidence": evidence]
        let data = (try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])) ?? Data()
        try? data.write(to: output.appendingPathComponent("native-ui.json"), options: .atomic)
        print(String(decoding: data, as: UTF8.self)); fflush(stdout)
        controller.stopObservation(); exit(checks.values.allSatisfy { $0 } ? 0 : 1)
    }

    static func snapshotPanel(_ window:NSWindow,to output:URL) throws {
        guard NSApp.activationPolicy() == .prohibited, let view = window.contentView?.superview else { throw EngineFailure("offscreen_required","面板截图须离屏运行") }
        view.layoutSubtreeIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in:view.bounds) else { throw EngineFailure("snapshot_failed","面板渲染失败") }
        view.cacheDisplay(in:view.bounds,to:bitmap)
        guard let data = bitmap.representation(using:.png,properties:[:]), data.count>3000 else { throw EngineFailure("snapshot_empty","面板截图没有有效内容") }
        try data.write(to:output,options:.atomic)
    }

    static func managementAndAI(_ controller:LibraryWindowController,output:URL) async throws -> [String:Bool] {
        var checks:[String:Bool]=[:]
        let content:JSON=["title":"文档管理与 AI 阅读验收","home":"first","pages":[["id":"first","title":"结论","blocks":[["type":"prose","text":"方案流量为 12.5，依据在第二页。"]]],["id":"second","title":"依据与数据","blocks":[["type":"table","headers":["指标","数值"],"rows":[["流量",12.5]]],["type":"code","language":"python","text":"flow = 12.5\nprint(flow)"]]]]]
        let result = try await controller.engine.call("build",["content":content,"kind":"portal","project":"产品验收"])
        guard let raw = result["document"] as? JSON, let doc = KnowledgeDocument(raw) else { throw EngineFailure("document_missing","管理样本未返回") }
        await controller.refresh(); await controller.open(documentID:doc.id,pageID:"second"); try await waitForDocument(controller)
        checks["management_primary_controls"] = controller.copyAIButton.isEnabled && controller.infoButton.isEnabled && controller.locationButton.isEnabled && controller.documentsTable.allowsMultipleSelection
        checks["management_sidebar_trash"] = controller.sidebar.numberOfRows == 8
        let paths = try await controller.engine.call("paths",["document_id":doc.id,"page_id":"second","version":1])
        checks["management_current_html_distinct_entry"] = (paths["current_html"] as? JSON)?["filename"] as? String == "second.html" && (paths["entry_html"] as? JSON)?["filename"] as? String == "index.html"
        let domain = ProcessInfo.processInfo.environment["LUMEN_DEFAULTS_DOMAIN"] ?? "isolated"
        let boardName = "lumen-test-"+domain+"-"+UUID().uuidString
        setenv("LUMEN_TEST_PASTEBOARD",boardName,1); let board = NSPasteboard(name:.init(boardName))
        defer { board.clearContents(); unsetenv("LUMEN_TEST_PASTEBOARD") }
        let material = try await controller.copyCurrentMaterial()
        checks["management_copy_ai_actual_named_pasteboard"] = board.string(forType:.string) == material["copy_text"] as? String && (material["scope"] as? String) == "page" && ((material["pages"] as? [JSON])?.first?["id"] as? String) == "second"
        let info = DocumentInformationController(library:controller,documentID:doc.id,pageID:"second",version:1); await info.load()
        checks["management_information_panel_real_paths"] = info.records.count >= 3 && info.records.contains { $0["filename"] as? String == "second.html" }
        if let window = info.window { try snapshotPanel(window,to:output.appendingPathComponent("document-information.png")) }
        let ai = AIReadingController(library:controller,documentID:doc.id,pageID:"second",version:1,scope:"document"); await ai.prepare()
        checks["management_ai_panel_all_pages"] = (ai.prepared?["coverage"] as? JSON)?["extracted_pages"] as? Int == 2
        if let window = ai.window { try snapshotPanel(window,to:output.appendingPathComponent("ai-reading.png")) }
        let renamed = try await controller.engine.call("rename",["document_id":doc.id,"title":"重新命名的文档","expected_version":1,"expected_metadata_revision":0])
        await controller.open(documentID:doc.id,pageID:"second"); try await waitForDocument(controller)
        checks["management_rename_native_readback"] = controller.selectedDocument?.title == "重新命名的文档" && controller.titleField.stringValue == "重新命名的文档"
        _ = try await js(controller.reader,"window.LumenReader.setEditing(true);const p=document.querySelector('td');p.textContent='未保存修改';p.dispatchEvent(new Event('input',{bubbles:true}));true")
        do { _ = try await controller.prepareSavedAction(documentIDs:[doc.id],purpose:"验收删除"); checks["management_unsaved_blocks_offscreen_action"] = false } catch { checks["management_unsaved_blocks_offscreen_action"] = (error as? EngineFailure)?.code == "unsaved_changes" }
        _ = try await js(controller.reader,"window.LumenReader.discardDraft();true"); await controller.open(documentID:doc.id,pageID:"second"); try await waitForDocument(controller)
        let renamedDoc = renamed["document"] as? JSON ?? [:]
        _ = try await controller.engine.call("trash",["document_id":doc.id,"expected_version":1,"expected_metadata_revision":renamedDoc["metadata_revision"] as? Int ?? 1])
        await controller.open(documentID:doc.id,pageID:"second"); try await waitForDocument(controller)
        checks["management_trash_disables_edit"] = controller.selectedDocument?.trashed == true && !controller.copyAIButton.isEnabled
        let trashMenu = controller.documentMenu(); checks["management_trash_restore_purge_menu"] = trashMenu.items.contains { $0.action == #selector(LibraryWindowController.restoreTrashedDocuments(_:)) } && trashMenu.items.contains { $0.action == #selector(LibraryWindowController.purgeDocuments(_:)) }
        _ = try await controller.engine.call("untrash",["document_id":doc.id]); await controller.open(documentID:doc.id,pageID:"second"); try await waitForDocument(controller)
        _ = try await controller.engine.call("rename-file",["document_id":doc.id,"page_id":"second","filename":"证据.html","expected_version":1])
        await controller.open(documentID:doc.id,pageID:"second"); try await waitForDocument(controller)
        let renamedBody = try await js(controller.reader,"document.body.innerText") as? String ?? ""
        checks["management_rename_html_native_load"] = controller.selectedPage?.absolutePath.hasSuffix("证据.html") == true && renamedBody.contains("12.5")
        await controller.open(documentID:doc.id,pageID:"second",version:1); try await waitForDocument(controller)
        checks["management_history_bound_paths"] = controller.viewingHistorical && controller.selectedPage?.absolutePath.hasSuffix("second.html") == true
        let oldMaterial = try await controller.copyCurrentMaterial(); checks["management_history_bound_ai"] = oldMaterial["version"] as? Int == 1
        await controller.open(documentID:doc.id,pageID:"second"); try await waitForDocument(controller)
        let other = try await controller.engine.call("build",["content":["title":"右键目标 B","pages":[["id":"b","title":"B","blocks":[["type":"prose","text":"B 的内容"]]]]],"kind":"report"])
        guard let b = KnowledgeDocument(other["document"] as? JSON ?? [:]), let a = controller.selectedDocument else { throw EngineFailure("document_missing","右键样本未返回") }
        controller.documents = [a,b]; controller.matches = []; controller.documentsTable.reloadData(); controller.documentsTable.selectRowIndexes(IndexSet(integer:1),byExtendingSelection:false)
        let menu = controller.documentMenu(row:1)
        let rename = menu.items.first { $0.action == #selector(LibraryWindowController.renameDocument(_:)) }
        checks["management_clicked_row_target"] = ((rename?.representedObject as? JSON)?["document_ids"] as? [String]) == [b.id]
        await controller.refresh(); await controller.open(documentID:doc.id,pageID:"second"); try await waitForDocument(controller)
        _ = try await controller.engine.call("settings",["values":["labels":["copy_ai":"热更新复制验收"]]])
        await controller.refresh(); checks["management_new_labels_runtime"] = controller.copyAIButton.title == "热更新复制验收"
        _ = try await controller.engine.call("settings",["values":["labels":["copy_ai":"复制给 AI"]]]); await controller.refresh()
        if let row=controller.documents.firstIndex(where: { $0.id == doc.id }), let promise=controller.tableView(controller.documentsTable,pasteboardWriterForRow:row) as? NSFilePromiseProvider {
            let target=output.appendingPathComponent("drag-export.zip")
            let error:Error?=await withCheckedContinuation { continuation in controller.filePromiseProvider(promise,writePromiseTo:target) { continuation.resume(returning:$0) } }
            checks["management_drag_export_actual_zip"]=error == nil && FileManager.default.fileExists(atPath:target.path)
        } else { checks["management_drag_export_actual_zip"]=false }
        checks["management_drag_in_registered"]=controller.documentsTable.registeredDraggedTypes.contains(.fileURL)
        let answerContent:JSON=["title":"阅读答案","home":"answer","pages":[["id":"answer","title":"结论","blocks":[["type":"prose","text":"流量为 12.5，依据为第二页的数据表。"]]]]]
        let answerUI=AIReadingController(library:controller,documentID:doc.id,pageID:"second",version:2,scope:"document")
        answerUI.accept(task:["id":"ask-native-transaction","status":"succeeded","params":["source_version":2,"read_scope":"document"],"result":["content":answerContent,"answer":"流量为 12.5","source_version":2,"coverage":["extracted_pages":2]]])
        let saved=try await answerUI.saveAnswerContent(title:"另存的阅读答案")
        checks["management_ai_answer_save_new_document"]=(saved["document"] as? JSON)?["id"] as? String != doc.id && (saved["document"] as? JSON)?["title"] as? String == "另存的阅读答案"
        let plan=try await answerUI.answerAppendPlan(), appended=try await answerUI.commitAnswerAppend(plan)
        checks["management_ai_answer_append_new_version"]=(appended["document"] as? JSON)?["version"] as? Int == 3 && ((plan["content"] as? JSON)?["pages"] as? [JSON])?.count == 3
        do { _ = try await answerUI.commitAnswerAppend(plan); checks["management_ai_answer_stale_commit_rejected"]=false } catch { checks["management_ai_answer_stale_commit_rejected"]=(error as? EngineFailure)?.code == "conflict" }
        checks["management_no_window_dock_clipboard_interference"] = NSApp.activationPolicy() == .prohibited && !NSApp.isActive && NSApp.windows.allSatisfy { !$0.isVisible && !$0.isKeyWindow }
        return checks
    }
}
